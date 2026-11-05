"""Tests for the queue and Celery priority the scheduler gives each test task.

On Redis a lower priority number is served first: tasks routed by hand
with the reserved priority, then release builds, then all other builds.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from alts.scheduler import CONFIG
# Imported as a module: pytest would try to collect `TestsScheduler`
from alts.scheduler import scheduling
from alts.shared.constants import TaskPriority


def _payload(
    index: int,
    release_build: bool = False,
    package_name: str = 'pkg',
    runner_type: str = 'opennebula',
) -> dict:
    return {
        'bs_task_id': f'task-{index}',
        'runner_type': runner_type,
        'dist_name': 'almalinux',
        'dist_version': '9',
        'dist_arch': 'x86_64',
        'package_name': package_name,
        'release_build': release_build,
    }


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
@pytest.mark.parametrize('release_build, expected_priority', [
    (True, TaskPriority.RELEASE_BUILD),
    (False, TaskPriority.REGULAR_BUILD),
])
def test_task_priority_depends_only_on_release(runner_type, tasks_count,
                                                release_build,
                                                expected_priority):
    tasks = [
        _payload(i, release_build, runner_type=runner_type)
        for i in range(tasks_count)
    ]
    assert _run_once(tasks) == [expected_priority] * tasks_count


def test_priorities_are_served_in_order():
    assert [int(p) for p in TaskPriority] == [0, 1, 2]
    assert (
        TaskPriority.RESERVED
        < TaskPriority.RELEASE_BUILD
        < TaskPriority.REGULAR_BUILD
    )


def test_redis_priority_steps_cover_every_priority():
    # Workers consume only the Redis priority lists listed in
    # `priority_steps`, so a priority outside them would never be served
    celery_config = CONFIG.get_celery_config_dict()
    if 'priority_steps' in celery_config['broker_transport_options']:
        steps = celery_config['broker_transport_options']['priority_steps']
        assert steps == [int(p) for p in TaskPriority]
    assert celery_config['task_queue_max_priority'] == len(TaskPriority)
    assert celery_config['task_default_priority'] == TaskPriority.REGULAR_BUILD


def test_redis_priority_steps_ignore_configured_max_priority():
    # Deployments still set the old `task_queue_max_priority` key; it must
    # not shrink the steps below what the scheduler publishes
    config = CONFIG.model_validate({
        **CONFIG.model_dump(),
        'broker_config': {'redis_host': 'redis'},
        'task_queue_max_priority': 1,
    })
    transport = config.get_celery_config_dict()['broker_transport_options']
    assert transport['priority_steps'] == [int(p) for p in TaskPriority]


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
