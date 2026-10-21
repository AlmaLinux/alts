from typing import Dict, List, Optional

import pytest

from alts.worker import tasks
from alts.worker.tasks import should_skip_test


class FakeRunner:
    """Records the test stages run_tests() runs instead of running them."""

    def __init__(self, *args, **kwargs):
        self.stages = []
        self.received_skipped_tests = None
        self.artifacts = {}
        self.uploaded_logs = None
        self.stats = {}
        self.skipped_third_party_tests = []

    def setup(self):
        pass

    def teardown(self):
        pass

    def run_system_info_commands(self):
        self.stages.append('system_info')

    def install_package(self, *args, **kwargs):
        self.stages.append('install_package')

    def run_package_integrity_tests(self, *args, **kwargs):
        self.stages.append('package_integrity_tests')

    def run_third_party_tests(
        self,
        *args,
        skipped_tests: Optional[List[str]] = None,
        **kwargs,
    ):
        self.stages.append('third_party_tests')
        self.received_skipped_tests = skipped_tests
        self.skipped_third_party_tests = ['third_party_test_smoke.sh']

    def uninstall_package(self, *args, **kwargs):
        self.stages.append('uninstall_package')


@pytest.mark.parametrize(
    'package_name, test_name, excluded_tests, skipped_tests, expected',
    [
        pytest.param(
            'foo',
            'install_package',
            {},
            [],
            False,
            id='nothing-skipped',
        ),
        pytest.param(
            'foo',
            'uninstall_package',
            {},
            ['uninstall_package'],
            True,
            id='skipped-for-task',
        ),
        pytest.param(
            'foo',
            'run_package_integrity_tests',
            {},
            ['package_integrity_tests'],
            True,
            id='skipped-integrity-tests',
        ),
        pytest.param(
            'foo',
            'run_third_party_tests',
            {},
            ['third_party_tests'],
            True,
            id='skipped-third-party-tests',
        ),
        pytest.param(
            'foo',
            'run_third_party_tests',
            {},
            ['third_party_test_smoke.sh'],
            False,
            id='single-third-party-test-skipped',
        ),
        pytest.param(
            'foo-devel',
            'run_package_integrity_tests',
            {'foo': ['run_package_integrity_tests']},
            [],
            True,
            id='excluded-for-package',
        ),
        pytest.param(
            'bar',
            'run_package_integrity_tests',
            {'foo': ['run_package_integrity_tests']},
            [],
            False,
            id='excluded-for-other-package',
        ),
        pytest.param(
            'foo',
            'run_third_party_tests',
            {'foo': ['run_third_party_tests']},
            [],
            True,
            id='excluded-third-party-tests',
        ),
    ],
)
def test_should_skip_test(
    package_name: str,
    test_name: str,
    excluded_tests: Dict[str, List[str]],
    skipped_tests: List[str],
    expected: bool,
):
    assert (
        should_skip_test(
            package_name,
            test_name,
            excluded_tests,
            skipped_tests,
        )
        is expected
    )


@pytest.mark.parametrize(
    'excluded_tests, skipped_tests, stages, summary_skipped_tests',
    [
        pytest.param(
            {},
            None,
            [
                'system_info',
                'install_package',
                'package_integrity_tests',
                'third_party_tests',
                'uninstall_package',
            ],
            ['third_party_test_smoke.sh'],
            id='nothing-skipped',
        ),
        pytest.param(
            {},
            ['package_integrity_tests', 'uninstall_package'],
            ['system_info', 'install_package', 'third_party_tests'],
            [
                'package_integrity_tests',
                'uninstall_package',
                'third_party_test_smoke.sh',
            ],
            id='skipped-for-task',
        ),
        pytest.param(
            {'foo': ['run_third_party_tests']},
            ['uninstall_package'],
            ['system_info', 'install_package', 'package_integrity_tests'],
            ['third_party_tests', 'uninstall_package'],
            id='excluded-and-skipped',
        ),
    ],
)
def test_run_tests_skips_tests(
    monkeypatch,
    excluded_tests: Dict[str, List[str]],
    skipped_tests: Optional[List[str]],
    stages: List[str],
    summary_skipped_tests: List[str],
):
    runners = []

    def create_runner(*args, **kwargs):
        runners.append(FakeRunner(*args, **kwargs))
        return runners[-1]

    monkeypatch.setitem(tasks.RUNNER_MAPPING, 'docker', create_runner)
    monkeypatch.setattr(tasks.time, 'sleep', lambda _: None)
    monkeypatch.setattr(tasks.CONFIG, 'enable_integrity_tests', True)
    summary = tasks.run_tests.run(
        {
            'task_id': 'task_id',
            'runner_type': 'docker',
            'dist_name': 'almalinux',
            'dist_version': '9',
            'dist_arch': 'x86_64',
            'repositories': [],
            'package_name': 'foo',
            'skipped_tests': skipped_tests,
        },
        excluded_tests,
    )
    runner = runners[0]
    assert runner.stages == stages
    if 'third_party_tests' in stages:
        assert runner.received_skipped_tests == (skipped_tests or [])
    assert summary['skipped_tests'] == summary_skipped_tests
