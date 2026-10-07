"""Tests for the Celery priority the scheduler gives each test task.

On Redis a lower priority number is served first: manually routed tasks,
then release builds, then builds with at most `small_build_max_tasks` test
tasks, then larger builds (PF-3736).
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from alts.scheduler import CONFIG
# Imported as a module: pytest would try to collect `TestsScheduler`
from alts.scheduler import scheduling
from alts.shared.constants import TaskPriority


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
        TaskPriority.RELEASE_BUILD,
        id='release-build',
    ),
    pytest.param(
        [_payload(i) for i in range(5)],
        TaskPriority.SMALL_BUILD,
        id='5-task-build',
    ),
    pytest.param(
        [_payload(i) for i in range(CONFIG.small_build_max_tasks)],
        TaskPriority.SMALL_BUILD,
        id='build-at-small-limit',
    ),
    pytest.param(
        [_payload(i) for i in range(50)],
        TaskPriority.LARGE_BUILD,
        id='50-task-build',
    ),
])
def test_task_priority_depends_on_build_size(test_tasks, expected_priority):
    assert _run_once(test_tasks) == [expected_priority] * len(test_tasks)


def test_priorities_are_served_in_order():
    assert (
        TaskPriority.MANUAL
        < TaskPriority.RELEASE_BUILD
        < TaskPriority.SMALL_BUILD
        < TaskPriority.LARGE_BUILD
    )


def test_redis_priority_steps_cover_every_priority():
    # Workers consume only the Redis priority lists listed in
    # `priority_steps`, so a priority outside them would never be served
    celery_config = CONFIG.get_celery_config_dict()
    if 'priority_steps' in celery_config['broker_transport_options']:
        steps = celery_config['broker_transport_options']['priority_steps']
        assert steps == [int(p) for p in TaskPriority]
    assert celery_config['task_queue_max_priority'] == len(TaskPriority)
    assert celery_config['task_default_priority'] == TaskPriority.SMALL_BUILD


def test_redis_priority_steps_ignore_configured_max_priority():
    # Deployments still set the old `task_queue_max_priority` key; it must
    # not shrink the steps below what the scheduler publishes
    config = CONFIG.model_validate({
        **CONFIG.model_dump(),
        'broker_config': {'redis_host': 'redis'},
        'task_queue_max_priority': 2,
    })
    transport = config.get_celery_config_dict()['broker_transport_options']
    assert transport['priority_steps'] == [int(p) for p in TaskPriority]
