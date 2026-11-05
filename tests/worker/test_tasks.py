"""Tests for the build check and the result callback of `run_tests`.

A test task of a build removed from the build system is skipped, and a
4xx answer to the result callback does not retry the whole task.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from alts.worker import tasks

BUILD_ID = '65a1b2c3d4e5f60718293a4b'
CALLBACK_HREF = f'/api/v1/tests/{BUILD_ID}/65a1b2c3d4e5f60718293a4c/2/result/'


def _response(status_code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = 'http://web_server:8000/'
    return response


def _task_params() -> dict:
    return {
        'task_id': 'task-1',
        'runner_type': 'docker',
        'dist_name': 'almalinux',
        'dist_version': '9',
        'dist_arch': 'x86_64',
        'repositories': [],
        'package_name': 'pkg',
        'callback_href': CALLBACK_HREF,
    }


@pytest.fixture
def runner_class():
    runner = MagicMock(artifacts={}, uploaded_logs=None, stats={})
    runner_class = MagicMock(return_value=runner)
    with patch.dict(tasks.RUNNER_MAPPING, {'docker': runner_class}), \
            patch.object(tasks.time, 'sleep'):
        yield runner_class


@pytest.mark.parametrize('status_code, expected', [
    pytest.param(404, False, id='404-missing'),
    pytest.param(200, True, id='200-exists'),
    pytest.param(500, True, id='500-unknown'),
    pytest.param(403, True, id='403-unknown'),
])
def test_build_exists_only_404_means_missing(status_code, expected):
    with patch.object(
        tasks.requests, 'get', return_value=_response(status_code),
    ) as get:
        assert tasks.build_exists(CALLBACK_HREF) is expected
    assert get.call_args.args[0] == (
        f'http://web_server:8000/api/v1/builds/{BUILD_ID}/'
    )


def test_build_exists_on_request_error():
    with patch.object(
        tasks.requests, 'get',
        side_effect=requests.ConnectionError('refused'),
    ):
        assert tasks.build_exists(CALLBACK_HREF) is True


@pytest.mark.parametrize('callback_href', [
    None,
    '',
    '/api/v1/some/other/callback/',
])
def test_build_exists_without_build_id(callback_href):
    with patch.object(tasks.requests, 'get') as get:
        assert tasks.build_exists(callback_href) is True
    get.assert_not_called()


def test_run_tests_skips_missing_build(runner_class):
    with patch.object(tasks, 'build_exists', return_value=False), \
            patch.object(tasks.requests, 'Session') as session:
        assert tasks.run_tests(_task_params()) is None
    runner_class.assert_not_called()
    session.assert_not_called()


@pytest.mark.parametrize('status_code', [400, 404, 422])
def test_run_tests_callback_4xx_is_not_retried(runner_class, status_code):
    with patch.object(tasks, 'build_exists', return_value=True), \
            patch.object(tasks.requests, 'Session') as session:
        session.return_value.post.return_value = _response(status_code)
        summary = tasks.run_tests(_task_params())
    runner_class.return_value.setup.assert_called_once()
    session.return_value.post.assert_called_once()
    assert summary is not None


@pytest.mark.parametrize('status_code', [500, 503])
def test_run_tests_callback_5xx_is_retried(runner_class, status_code):
    # Called directly, a task raises the exception it would retry on
    with patch.object(tasks, 'build_exists', return_value=True), \
            patch.object(tasks.requests, 'Session') as session, \
            patch.object(tasks.run_tests, 'retry', wraps=tasks.run_tests.retry) as retry:
        session.return_value.post.return_value = _response(status_code)
        with pytest.raises(requests.HTTPError):
            tasks.run_tests(_task_params())
    retry.assert_called_once()
