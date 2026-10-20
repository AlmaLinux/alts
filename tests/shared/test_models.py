from typing import List, Optional

import pytest

from alts.shared.models import TaskRequestPayload


@pytest.mark.parametrize(
    'skipped_tests',
    [
        pytest.param(None, id='nothing-skipped'),
        pytest.param(
            ['uninstall_package', 'third_party_test_*.bats'],
            id='skipped-tests',
        ),
    ],
)
def test_task_request_payload_skipped_tests(
    skipped_tests: Optional[List[str]],
):
    # The build system sends null for the tasks that skip nothing
    payload = TaskRequestPayload(
        bs_task_id=1,
        runner_type='docker',
        dist_name='almalinux',
        dist_version='9',
        dist_arch='x86_64',
        package_name='foo',
        skipped_tests=skipped_tests,
    )
    assert payload.model_dump()['skipped_tests'] == skipped_tests
