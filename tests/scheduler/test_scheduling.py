"""Tests for the queue and Celery priority the scheduler gives each test task.

A test task runs at the priority the build system sent (PF-4420), whatever
the build size, and a release build's tests one level above development
tests of the same level. The test config uses RabbitMQ, where a higher
priority number is served first.
"""
import threading
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from alts.scheduler import CONFIG
# Imported as a module: pytest would try to collect `TestsScheduler`
from alts.scheduler import scheduling
from alts.shared.constants import (
    EMERGENCY_TASK_LEVEL,
    RELEASE_BUILD_TOP_LEVEL,
    TASK_PRIORITY_LEVELS,
)
from alts.shared.models import get_task_priority_level


def _payload(
    index: int,
    release_build: bool = False,
    package_name: str = 'pkg',
    runner_type: str = 'opennebula',
    priority: Optional[int] = None,
) -> dict:
    payload = {
        'bs_task_id': f'task-{index}',
        'runner_type': runner_type,
        'dist_name': 'almalinux',
        'dist_version': '9',
        'dist_arch': 'x86_64',
        'package_name': package_name,
        'release_build': release_build,
    }
    if priority is not None:
        payload['priority'] = priority
    return payload


def _run_once(test_tasks: list, field: str = 'priority') -> list:
    """Run one scheduler cycle and return the published `field` values."""
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
        call.kwargs[field]
        for call in run_tests.apply_async.call_args_list
    ]


@pytest.mark.parametrize('runner_type', ['docker', 'opennebula'])
@pytest.mark.parametrize('tasks_count', [1, 50])
@pytest.mark.parametrize('release_build, priority, expected_priority', [
    # without a priority from the build system: release builds get the
    # git tag build default
    pytest.param(True, None, 2, id='release-build'),
    # and the other builds MEDIUM
    pytest.param(False, None, 1, id='regular-build'),
    # the low_priority_build tag wins over the release build flag
    pytest.param(True, 0, 0, id='low-priority-release-build'),
    # alt-php on an unweighted platform: release 4, development 3
    pytest.param(True, 4, 3, id='weighted-release-build'),
    pytest.param(False, 3, 2, id='weighted-development-build'),
    pytest.param(True, 23, 8, id='top-rule-priority'),
    pytest.param(False, 22, 7, id='top-development-priority'),
    pytest.param(True, 31, EMERGENCY_TASK_LEVEL, id='emergency'),
])
def test_task_priority_ignores_build_size(runner_type, tasks_count,
                                          release_build, priority,
                                          expected_priority):
    tasks = [
        _payload(i, release_build, runner_type=runner_type,
                 priority=priority)
        for i in range(tasks_count)
    ]
    assert _run_once(tasks) == [expected_priority] * tasks_count


@pytest.mark.parametrize('bs_priority, level', [
    (-1, 0),
    # low_priority_build tag
    (0, 0),
    # MEDIUM
    (1, 1),
    # git tag build default
    (2, 1),
    (3, 2),
    (4, 2),
    # routine work on CL7-CL9
    (5, 3),
    (6, 3),
    (7, 4),
    (10, 4),
    (11, 5),
    (14, 5),
    (15, 6),
    (18, 6),
    (19, 7),
    (22, 7),
    # the top weighted priority of a release build, or the high tag
    (23, 8),
    (24, EMERGENCY_TASK_LEVEL),
    (31, EMERGENCY_TASK_LEVEL),
])
def test_task_priority_level(bs_priority, level):
    assert get_task_priority_level(bs_priority) == level


@pytest.mark.parametrize('bs_priority, level', [
    # the low_priority_build tag keeps level 0
    (0, 0),
    (2, 2),
    (4, 3),
    (6, 4),
    (20, 8),
    # never moved to EMERGENCY
    (23, RELEASE_BUILD_TOP_LEVEL),
    (31, EMERGENCY_TASK_LEVEL),
])
def test_release_build_task_priority_level(bs_priority, level):
    assert get_task_priority_level(bs_priority, release_build=True) == level


@pytest.mark.parametrize('os_weight', range(1, 7))
@pytest.mark.parametrize('project_weight', range(1, 5))
def test_release_tests_go_ahead_of_development_tests(os_weight,
                                                     project_weight):
    # build system weighted priorities (PF-4420): os * project for a
    # release build, os * project - 1 (at most 22) for the others, never
    # below the default of 2 (release) and 1 (development)
    weight = os_weight * project_weight
    release = max(min(weight, 23), 2)
    development = max(min(weight - 1, 22), 1)
    assert get_task_priority_level(release, release_build=True) > \
        get_task_priority_level(development)


@pytest.mark.parametrize('release_build', [False, True])
def test_levels_keep_build_system_order(release_build):
    levels = [
        get_task_priority_level(priority, release_build)
        for priority in range(-1, 33)
    ]
    assert levels == sorted(levels)


@pytest.mark.parametrize('bs_priority', range(-1, 33))
def test_release_level_never_below_development_level(bs_priority):
    assert get_task_priority_level(bs_priority, release_build=True) >= \
        get_task_priority_level(bs_priority)


@pytest.mark.parametrize('release, development', [
    # the same level: release goes first, even with a lower priority
    (3, 4),
    (7, 10),
])
def test_release_goes_ahead_within_a_level(release, development):
    assert get_task_priority_level(release, release_build=True) > \
        get_task_priority_level(development)


@pytest.mark.parametrize('release, development', [
    # the next level up: a tie, by design
    (8, 11),
    (16, 22),
])
def test_release_ties_the_next_level(release, development):
    assert get_task_priority_level(release, release_build=True) == \
        get_task_priority_level(development)


def test_top_development_priority_below_top_release_priority():
    # why 23 has a level of its own: development builds stop at 22
    assert get_task_priority_level(22) < \
        get_task_priority_level(23, release_build=True)


def test_levels_fit_redis():
    # Redis clamps a message priority to 0..9
    assert TASK_PRIORITY_LEVELS <= 10
    levels = {get_task_priority_level(priority) for priority in range(32)}
    assert levels == set(range(TASK_PRIORITY_LEVELS))


def test_rabbitmq_priorities():
    celery_config = CONFIG.get_celery_config_dict()
    assert celery_config['task_queue_max_priority'] == TASK_PRIORITY_LEVELS
    # MEDIUM
    assert celery_config['task_default_priority'] == 1
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
    assert celery_config['task_default_priority'] == EMERGENCY_TASK_LEVEL - 1


@pytest.mark.parametrize('release_build, priority, redis_priority', [
    # alt-php on an unweighted platform: Redis serves the lower list first
    (True, 4, EMERGENCY_TASK_LEVEL - 3),
    (False, 3, EMERGENCY_TASK_LEVEL - 2),
    (True, 31, 0),
])
def test_redis_scheduler_puts_release_tests_first(release_build, priority,
                                                   redis_priority):
    config = _redis_config()
    with patch.object(scheduling, 'CONFIG', config):
        task = _payload(0, release_build, priority=priority)
        assert _run_once([task]) == [redis_priority]


def test_redis_priority_steps_ignore_configured_max_priority():
    # Deployments still set the old `task_queue_max_priority` key; it must
    # not shrink the steps below what the scheduler publishes
    config = _redis_config(task_queue_max_priority=1)
    transport = config.get_celery_config_dict()['broker_transport_options']
    assert transport['priority_steps'] == list(range(TASK_PRIORITY_LEVELS))


PACKAGE_QUEUE_SUFFIXES = {
    'acme-foo.*': 'foo',
    'acme-bar[0-9]+(-.*)?': 'bar',
    # Never reached for acme-foo/acme-bar: the first matching entry wins
    'acme-.*': 'acme',
}


@pytest.fixture
def package_queue_suffixes(monkeypatch):
    monkeypatch.setattr(
        CONFIG, 'package_queue_suffixes', PACKAGE_QUEUE_SUFFIXES,
    )


@pytest.mark.usefixtures('package_queue_suffixes')
@pytest.mark.parametrize('runner_type', ['docker', 'opennebula'])
@pytest.mark.parametrize('release_build', [False, True])
@pytest.mark.parametrize('package_name, suffix', [
    ('acme-foo1', 'foo'),
    ('acme-foo2-extra', 'foo'),
    ('acme-bar1', 'bar'),
    ('acme-bar1-extra', 'bar'),
    ('acme-baz', 'acme'),
])
def test_package_goes_to_its_queue(runner_type, release_build,
                                   package_name, suffix):
    task = _payload(0, release_build, package_name, runner_type)
    assert _run_once([task], 'queue') == [f'{runner_type}-x86_64-{suffix}']


@pytest.mark.usefixtures('package_queue_suffixes')
@pytest.mark.parametrize('package_name', [
    'pkg',
    'other-foo1',
    'foo-acme',
    # The regex must match the whole name, case-sensitively
    'my-acme-foo1',
    'ACME-FOO1',
])
def test_unmatched_packages_keep_cost_queue(package_name):
    task = _payload(0, package_name=package_name)
    assert _run_once([task], 'queue') == ['opennebula-x86_64-1']


def test_no_package_queue_suffixes_by_default():
    assert CONFIG.model_fields['package_queue_suffixes'].default == {}
    task = _payload(0, package_name='acme-foo1')
    assert _run_once([task], 'queue') == ['opennebula-x86_64-1']


@pytest.mark.parametrize('suffixes', [
    {'acme-foo[': 'foo'},
    {'acme-foo.*': ''},
])
def test_invalid_package_queue_suffixes_are_rejected(suffixes):
    with pytest.raises(ValueError):
        CONFIG.model_validate({
            **CONFIG.model_dump(),
            'package_queue_suffixes': suffixes,
        })


@pytest.mark.parametrize('runner_type, release_build, queue', [
    ('docker', False, 'docker-x86_64-0'),
    ('docker', True, 'docker-x86_64-1'),
    ('opennebula', False, 'opennebula-x86_64-1'),
    ('opennebula', True, 'opennebula-x86_64-2'),
])
def test_queue_cost_comes_from_runner(runner_type, release_build, queue):
    tasks = [
        _payload(i, release_build, runner_type=runner_type)
        for i in range(3)
    ]
    assert _run_once(tasks, 'queue') == [queue] * len(tasks)


def test_package_queue_gets_priority_support():
    # Workers do not declare the package queues: Celery creates them on
    # publish and must still give them the priority argument
    from alts.worker.app import celery_app
    assert celery_app.conf.task_create_missing_queues
    queue = celery_app.amqp.queues['docker-x86_64-foo']
    assert queue.routing_key == 'docker-x86_64-foo'
    assert queue.queue_arguments == {
        'x-max-priority': CONFIG.task_queue_max_priority,
    }
