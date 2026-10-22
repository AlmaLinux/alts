from pathlib import Path
from typing import Iterator, List, Tuple
from unittest.mock import MagicMock

import pytest

from alts.shared.constants import COMMAND_TIMEOUT_EXIT_CODE
from alts.shared.exceptions import AbortedTestTask, InstallPackageError
from alts.worker.runners import DockerRunner
from alts.worker.runners.base import describe_mirror_error, find_mirror_error

MIRROR_ERROR_OUTPUT = (
    'fatal: [alts_docker]: FAILED! => {"changed": false, "msg": "Failed to '
    "download metadata for repo 'appstream': Cannot download repomd.xml: "
    'Curl error (28): Timeout was reached for https://repo.almalinux.org/'
    'almalinux/9/AppStream/x86_64/os/repodata/repomd.xml", "rc": 1}'
)
DEPSOLVE_ERROR_OUTPUT = (
    'fatal: [alts_docker]: FAILED! => {"changed": false, "failures": [], '
    '"msg": "Depsolve Error occurred: \\n Problem: conflicting requests\\n '
    '- nothing provides libfoo.so.1()(64bit) needed by foo-1.0-1.el9", '
    '"rc": 1}'
)


def task_is_aborted():
    return False


@pytest.mark.parametrize(
    'output',
    [
        pytest.param(MIRROR_ERROR_OUTPUT, id='ansible-dnf-module'),
        pytest.param(
            'Errors during downloading metadata for repository \'baseos\':\n'
            '  - Curl error (23): Failed writing received data to disk/'
            'application for https://repo.almalinux.org/repomd.xml',
            id='curl-error-23',
        ),
        pytest.param(
            "Error: Failed to download metadata for repo 'epel': "
            'Cannot download repomd.xml: Cannot download '
            'repodata/repomd.xml: All mirrors were tried',
            id='all-mirrors-were-tried',
        ),
        pytest.param(
            'Error: Failed to download packages: foo-1.0-1.el9.x86_64: '
            'Cannot download, all mirrors were already tried without success',
            id='failed-to-download-packages',
        ),
        pytest.param(
            ">>> Status code: 503 for https://mirror/repomd.xml\n"
            "Loading repository 'baseos' has failed",
            id='dnf5-loading-repository',
        ),
        pytest.param(
            "Loading repository 'AlmaLinux 10 - AppStream' has failed",
            id='loading-repository-with-spaces',
        ),
        pytest.param(
            'fatal: [alts_docker]: FAILED! => changed=false\n'
            "  msg: 'Failed to download metadata for repo ''epel'': Cannot "
            'download repomd.xml: Cannot download repodata/repomd.xml: All '
            "mirrors were tried'",
            id='albs-94195-update-all-packages',
        ),
        pytest.param(
            'Error: Failed to synchronize cache for repo \'AppStream\'',
            id='dnf-synchronize-cache',
        ),
        pytest.param(
            'Error: Cannot retrieve repository metadata (repomd.xml) for '
            'repository: base. Please verify its path and try again',
            id='yum-repository-metadata',
        ),
        pytest.param(
            'http://vault.centos.org/7.9.2009/os/x86_64/Packages/foo.rpm: '
            '[Errno 14] curl#7 - "Failed connect to vault.centos.org:80"\n'
            'Trying other mirror.',
            id='yum-errno-14',
        ),
        pytest.param(
            'E: Failed to fetch http://deb.debian.org/debian/dists/'
            'bookworm/InRelease  Temporary failure resolving '
            "'deb.debian.org'",
            id='apt-failed-to-fetch',
        ),
        pytest.param(
            'E: Failed to fetch store:/var/lib/apt/lists/partial/foo  '
            'Hash Sum mismatch',
            id='apt-hash-sum-mismatch',
        ),
    ],
)
def test_find_mirror_error(output: str):
    assert find_mirror_error(output) is not None


@pytest.mark.parametrize(
    'output',
    [
        pytest.param(DEPSOLVE_ERROR_OUTPUT, id='depsolve'),
        pytest.param(
            'No match for argument: foo\nError: Unable to find a match: foo',
            id='no-match',
        ),
        pytest.param(
            'Transaction test error:\n  file /usr/bin/foo from install of '
            'foo-1.0-1.el9.x86_64 conflicts with file from package '
            'bar-1.0-1.el9.x86_64',
            id='file-conflict',
        ),
        pytest.param(
            'Error in PREIN scriptlet in rpm package foo\n'
            'error: foo-1.0-1.el9.x86_64: install failed',
            id='scriptlet',
        ),
        pytest.param(
            'Timeout occurred when running ansible command: '
            '"ansible-playbook -i hosts playbook.yml"',
            id='command-timeout',
        ),
        pytest.param('', id='empty'),
    ],
)
def test_find_mirror_error_ignores_other_errors(output: str):
    assert find_mirror_error(output) is None


def test_describe_mirror_error():
    match = find_mirror_error('foo\nbar Curl error (6): Could not resolve\nbaz')
    assert describe_mirror_error(match) == 'Curl error (6): Could not resolve'
    match = find_mirror_error('Curl error (6): ' + 'x' * 500)
    assert len(describe_mirror_error(match)) == 200


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[DockerRunner]:
    runner = DockerRunner('test_id', task_is_aborted, 'almalinux', '9')
    # The commands are only run when the working directory exists
    runner._work_dir = str(tmp_path)
    yield runner
    # Don't let the runner try to stop the environment on deletion
    runner._work_dir = None


@pytest.fixture
def jitter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    # No jitter, so that the delays are exactly MIRROR_RETRY_DELAYS
    uniform = MagicMock(return_value=1)
    monkeypatch.setattr('alts.worker.runners.base.random.uniform', uniform)
    return uniform


@pytest.fixture
def sleep(monkeypatch: pytest.MonkeyPatch, jitter: MagicMock) -> MagicMock:
    sleep = MagicMock()
    monkeypatch.setattr('alts.worker.runners.base.time.sleep', sleep)
    return sleep


def mock_ansible(
    runner: DockerRunner,
    results: List[Tuple[int, str, str]],
) -> MagicMock:
    run_ansible_command = MagicMock(side_effect=results)
    runner.run_ansible_command = run_ansible_command
    return run_ansible_command


def sleeps(sleep: MagicMock) -> List[int]:
    return [call.args[0] for call in sleep.call_args_list]


class TestMirrorRetries:
    def test_succeeds_on_second_attempt(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ):
        ansible = mock_ansible(
            runner,
            [(2, MIRROR_ERROR_OUTPUT, ''), (0, 'installed', '')],
        )
        exit_code, stdout, stderr = runner.install_package_no_log('foo')
        assert ansible.call_count == 2
        assert sleeps(sleep) == [5]
        assert exit_code == 0
        assert stdout.startswith(
            'Attempt 1 of 4 failed with exit code 2 on a mirror error, '
            'retried in 5 seconds: Failed to download metadata for repo'
        )
        assert stdout.endswith('\n\ninstalled')
        assert stderr == ''
        assert (
            'Installation of foo failed on a mirror error on attempt 1 of 4, '
            'retrying in 5 seconds. Signature "Failed to download metadata '
            'for repo" matched' in caplog.text
        )

    def test_gives_up_after_three_retries(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        results = [
            (2, f'attempt {i}: {MIRROR_ERROR_OUTPUT}', 'err')
            for i in range(1, 5)
        ]
        ansible = mock_ansible(runner, results)
        exit_code, stdout, stderr = runner.install_package_no_log('foo')
        assert ansible.call_count == 4
        assert sleeps(sleep) == [5, 10, 15]
        assert exit_code == 2
        assert stderr == 'err'
        notes, last_output = stdout.split('\n\n')
        assert last_output == results[-1][1]
        assert len(notes.splitlines()) == 3

    def test_delays_have_jitter(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
        jitter: MagicMock,
    ):
        jitter.return_value = 1.2
        mock_ansible(
            runner,
            [(2, MIRROR_ERROR_OUTPUT, '')] * 3 + [(0, 'installed', '')],
        )
        exit_code, stdout, _ = runner.install_package_no_log('foo')
        assert exit_code == 0
        assert sleeps(sleep) == [6, 12, 18]
        assert {call.args for call in jitter.call_args_list} == {(0.75, 1.25)}
        assert 'retried in 6 seconds' in stdout

    def test_no_retry_on_other_errors(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        ansible = mock_ansible(runner, [(2, DEPSOLVE_ERROR_OUTPUT, '')])
        result = runner.install_package_no_log('foo')
        assert ansible.call_count == 1
        sleep.assert_not_called()
        assert result == (2, DEPSOLVE_ERROR_OUTPUT, '')

    def test_no_retry_on_timeout(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        ansible = mock_ansible(
            runner,
            [(COMMAND_TIMEOUT_EXIT_CODE, MIRROR_ERROR_OUTPUT, 'timeout')],
        )
        exit_code, _, _ = runner.install_package_no_log('foo')
        assert ansible.call_count == 1
        sleep.assert_not_called()
        assert exit_code == COMMAND_TIMEOUT_EXIT_CODE

    def test_aborted_between_attempts(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        runner._task_is_aborted = MagicMock(side_effect=[False, True])
        ansible = mock_ansible(
            runner,
            [(2, MIRROR_ERROR_OUTPUT, ''), (0, 'installed', '')],
        )
        with pytest.raises(AbortedTestTask):
            runner.install_package_no_log('foo')
        assert ansible.call_count == 1
        assert sleeps(sleep) == [5]
        assert runner.already_aborted

    def test_install_package_artifacts(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        mock_ansible(
            runner,
            [(2, MIRROR_ERROR_OUTPUT, ''), (2, DEPSOLVE_ERROR_OUTPUT, 'err')],
        )
        with pytest.raises(InstallPackageError):
            runner.install_package('foo')
        assert sleeps(sleep) == [5]
        artifact = runner.artifacts['install_package']
        assert artifact['exit_code'] == 2
        assert artifact['stderr'] == 'err'
        assert artifact['stdout'].startswith('Attempt 1 of 4 failed')
        assert artifact['stdout'].endswith(f'\n\n{DEPSOLVE_ERROR_OUTPUT}')

    def test_initial_provision(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        ansible = mock_ansible(
            runner,
            [
                (2, MIRROR_ERROR_OUTPUT, ''),
                (2, MIRROR_ERROR_OUTPUT, ''),
                (0, 'provisioned', ''),
            ],
        )
        runner.initial_provision()
        assert ansible.call_count == 2 + 1
        assert sleeps(sleep) == [5, 10]
        artifact = runner.artifacts['initial_provision']
        assert artifact['exit_code'] == 0
        assert artifact['stdout'].startswith('Attempt 1 of 4 failed')
        assert 'Attempt 2 of 4 failed' in artifact['stdout']
        assert artifact['stdout'].endswith('\n\nprovisioned')

    def test_uninstall_package(
        self,
        runner: DockerRunner,
        sleep: MagicMock,
    ):
        runner.detect_protected_packages = MagicMock(return_value=[])
        ansible = mock_ansible(
            runner,
            [(2, MIRROR_ERROR_OUTPUT, ''), (0, 'uninstalled', '')],
        )
        exit_code, stdout, _ = runner._uninstall_package('foo')
        assert ansible.call_count == 2
        assert sleeps(sleep) == [5]
        assert exit_code == 0
        assert stdout.endswith('\n\nuninstalled')
