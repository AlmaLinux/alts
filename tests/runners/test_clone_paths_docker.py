"""Container-side path-collision regression tests.

DockerRunner copies the locally-cloned QA tree into the container.
With the host-aware layout, two QA upstreams must be copied to two
distinct paths inside the container, matching the layout the test
runner expects when running tests by ``remote_workdir``.
"""
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alts.worker import CONFIG
from alts.worker.runners.docker import DockerRunner


GERRIT_QA = 'ssh://gerrit.cloudlinux.com:29418/QA'
GITLAB_QA = 'git@gitlab.corp.cloudlinux.com:clos/ci-tools/QA.git'


def _make_runner(monkeypatch, env_name='env-x'):
    runner = DockerRunner.__new__(DockerRunner)
    runner._logger = logging.getLogger('test')
    runner._env_name = env_name  # env_name is a read-only property
    runner.exec_command = MagicMock(return_value=(0, '', ''))
    runner.copy = MagicMock(return_value=(0, '', ''))

    fake_local_clone = Path('/tmp/fake-local-clone')
    monkeypatch.setattr(
        'alts.worker.runners.base.BaseRunner.clone_third_party_repo',
        lambda self, repo_url, git_ref: fake_local_clone,
    )
    return runner


class TestDockerCopyDestinationIsHostAware:
    def test_gerrit_qa_copy_target(self, monkeypatch):
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GERRIT_QA, 'master')

        expected_dest = (
            f'env-x:{CONFIG.tests_base_dir}/gerrit.cloudlinux.com/QA'
        )
        runner.copy.assert_called_once()
        copy_args = runner.copy.call_args.args[0]
        assert copy_args[1] == expected_dest

    def test_gitlab_qa_copy_target(self, monkeypatch):
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GITLAB_QA, 'master')

        expected_dest = (
            f'env-x:{CONFIG.tests_base_dir}'
            '/gitlab.corp.cloudlinux.com/clos/ci-tools/QA'
        )
        runner.copy.assert_called_once()
        assert runner.copy.call_args.args[0][1] == expected_dest

    def test_parent_directory_is_created_in_container(self, monkeypatch):
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GITLAB_QA, 'master')

        expected_parent = (
            f'{CONFIG.tests_base_dir}'
            '/gitlab.corp.cloudlinux.com/clos/ci-tools'
        )
        runner.exec_command.assert_called_once_with(
            'mkdir', '-p', expected_parent,
        )

    def test_two_qa_upstreams_get_distinct_container_paths(self, monkeypatch):
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GERRIT_QA, 'master')
        gerrit_dest = runner.copy.call_args_list[0].args[0][1]
        runner.copy.reset_mock()
        runner.exec_command.reset_mock()
        runner.clone_third_party_repo(GITLAB_QA, 'master')
        gitlab_dest = runner.copy.call_args_list[0].args[0][1]

        assert gerrit_dest != gitlab_dest
        # Neither destination is the bare basename path.
        bad = f'env-x:{CONFIG.tests_base_dir}/QA'
        assert gerrit_dest != bad
        assert gitlab_dest != bad
