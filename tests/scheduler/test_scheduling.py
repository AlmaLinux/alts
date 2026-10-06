"""Tests for the Celery priority the scheduler gives each test task.

On Redis a lower priority number is served first: release builds go
first, then builds with at most `small_build_max_tasks` test tasks, then
larger builds (PF-3736).
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from alts.scheduler import CONFIG
# Imported as a module: pytest would try to collect `TestsScheduler`
from alts.scheduler import scheduling
from alts.shared.models import CeleryConfig


def _payload(index: int, release_build: bool = False) -> dict:
    return {
        'bs_task_id': f'task-{index}',
        'runner_type': 'opennebula',
        'dist_name': 'almalinux',
        'dist_version': '9',
        'dist_arch': 'x86_64',
        'package_name': 'pkg',
        'release_build': release_build,
    }


def _run_once(test_tasks: list) -> list:
    """Run one scheduler cycle and return the published priorities."""
    terminated = threading.Event()
    graceful = threading.Event()

    def stop(_seconds):
        terminated.set()
        graceful.set()

    scheduler = scheduling.TestsScheduler(terminated, graceful, MagicMock())
    with patch.object(
        scheduler, 'get_available_test_tasks', return_value=test_tasks,
    ), patch.object(scheduling, 'run_tests') as run_tests, \
            patch.object(scheduling, 'Session'), \
            patch.object(scheduling.time, 'sleep', side_effect=stop):
        scheduler.run()
    return [
        call.kwargs['priority']
        for call in run_tests.apply_async.call_args_list
    ]


@pytest.mark.parametrize('test_tasks, expected_priority', [
    pytest.param(
        [_payload(i, release_build=True) for i in range(50)],
        CONFIG.release_build_priority,
        id='release-build',
    ),
    pytest.param(
        [_payload(i) for i in range(5)],
        CONFIG.task_default_priority,
        id='5-task-build',
    ),
    pytest.param(
        [_payload(i) for i in range(CONFIG.small_build_max_tasks)],
        CONFIG.task_default_priority,
        id='build-at-small-limit',
    ),
    pytest.param(
        [_payload(i) for i in range(50)],
        CONFIG.large_build_priority,
        id='50-task-build',
    ),
])
def test_task_priority_depends_on_build_size(test_tasks, expected_priority):
    assert _run_once(test_tasks) == [expected_priority] * len(test_tasks)


def test_small_builds_are_served_after_release_and_before_large():
    assert (
        CONFIG.release_build_priority
        < CONFIG.task_default_priority
        < CONFIG.large_build_priority
    )


def test_default_priority_steps_cover_every_published_priority():
    # Workers consume only the Redis priority lists listed in
    # `priority_steps`, so a priority outside them would never be served
    fields = CeleryConfig.model_fields
    steps = range(fields['task_queue_max_priority'].default)
    for name in (
        'release_build_priority',
        'task_default_priority',
        'large_build_priority',
    ):
        assert fields[name].default in steps
