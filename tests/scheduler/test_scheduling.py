"""Tests for the Celery priority the scheduler gives each test task.

A test task runs at the priority the build system sent (PF-4420). At the
same level, builds with at most `small_build_max_tasks` test tasks go before
larger builds (PF-3736), except at EMERGENCY. The test config uses RabbitMQ,
where a higher priority number is served first.
"""
import threading
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from alts.scheduler import CONFIG
# Imported as a module: pytest would try to collect `TestsScheduler`
from alts.scheduler import scheduling
from alts.shared.constants import EMERGENCY_TASK_LEVEL, TASK_PRIORITY_LEVELS
from alts.shared.models import get_task_priority_level


def _payload(
    index: int,
    release_build: bool = False,
    priority: Optional[int] = None,
) -> dict:
    payload = {
        'bs_task_id': f'task-{index}',
        'runner_type': 'opennebula',
        'dist_name': 'almalinux',
        'dist_version': '9',
        'dist_arch': 'x86_64',
        'package_name': 'pkg',
        'release_build': release_build,
    }
    if priority is not None:
        payload['priority'] = priority
    return payload


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
    # without a priority from the build system: release builds are HIGH
    pytest.param(
        [_payload(i, release_build=True) for i in range(50)],
        6,
        id='release-build',
    ),
    # and the other builds MEDIUM
    pytest.param(
        [_payload(i) for i in range(5)],
        3,
        id='5-task-build',
    ),
    pytest.param(
        [_payload(i) for i in range(CONFIG.small_build_max_tasks)],
        3,
        id='build-at-small-limit',
    ),
    pytest.param(
        [_payload(i) for i in range(50)],
        2,
        id='50-task-build',
    ),
    # the build system priority wins over the release build flag
    pytest.param(
        [_payload(i, release_build=True, priority=0) for i in range(5)],
        1,
        id='low-priority-release-build',
    ),
    pytest.param(
        [_payload(i, priority=23) for i in range(5)],
        7,
        id='top-rule-priority',
    ),
    # EMERGENCY is never reordered by build size
    pytest.param(
        [_payload(i, priority=31) for i in range(50)],
        EMERGENCY_TASK_LEVEL,
        id='emergency-large-build',
    ),
])
def test_task_priority(test_tasks, expected_priority):
    assert _run_once(test_tasks) == [expected_priority] * len(test_tasks)


@pytest.mark.parametrize('bs_priority, small_build, level', [
    (0, False, 0),
    (5, True, 1),
    (6, False, 2),
    (11, True, 3),
    (12, False, 4),
    (18, False, 6),
    (23, True, 7),
    (24, False, EMERGENCY_TASK_LEVEL),
    (31, True, EMERGENCY_TASK_LEVEL),
    (-1, False, 0),
])
def test_task_priority_level(bs_priority, small_build, level):
    assert get_task_priority_level(bs_priority, small_build) == level


def test_small_build_goes_first_only_inside_a_band():
    # a large build of a higher band still goes before a small build
    assert get_task_priority_level(12, small_build=False) > \
        get_task_priority_level(11, small_build=True)
    assert get_task_priority_level(11, small_build=True) > \
        get_task_priority_level(11, small_build=False)


def test_rabbitmq_priorities():
    celery_config = CONFIG.get_celery_config_dict()
    assert celery_config['task_queue_max_priority'] == TASK_PRIORITY_LEVELS
    # MEDIUM small build
    assert celery_config['task_default_priority'] == 3
    assert CONFIG.get_celery_priority(EMERGENCY_TASK_LEVEL) == \
        EMERGENCY_TASK_LEVEL
    assert CONFIG.get_celery_priority(99) == EMERGENCY_TASK_LEVEL


def _redis_config(**fields):
    return CONFIG.model_validate({
        **CONFIG.model_dump(),
        'broker_config': {'redis_host': 'redis'},
        **fields,
    })


def test_redis_priorities_are_inverted():
    # Workers consume only the Redis priority lists listed in
    # `priority_steps`, so a priority outside them would never be served
    config = _redis_config()
    celery_config = config.get_celery_config_dict()
    steps = celery_config['broker_transport_options']['priority_steps']
    assert steps == list(range(TASK_PRIORITY_LEVELS))
    # Redis serves the lowest number first
    assert config.get_celery_priority(EMERGENCY_TASK_LEVEL) == 0
    assert config.get_celery_priority(0) == EMERGENCY_TASK_LEVEL
    assert celery_config['task_default_priority'] == EMERGENCY_TASK_LEVEL - 3


def test_redis_priority_steps_ignore_configured_max_priority():
    # Deployments still set the old `task_queue_max_priority` key; it must
    # not shrink the steps below what the scheduler publishes
    config = _redis_config(task_queue_max_priority=2)
    transport = config.get_celery_config_dict()['broker_transport_options']
    assert transport['priority_steps'] == list(range(TASK_PRIORITY_LEVELS))
