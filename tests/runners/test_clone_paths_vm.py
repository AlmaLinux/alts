"""VM-side path-collision regression tests.

The SSH-runner override of ``clone_third_party_repo`` decides where the
QA repo lives inside the test VM. With the old basename-only path, two
upstreams (gerrit ``QA`` and gitlab ``QA.git``) collided at ``/opt/QA``.
These tests pin the host-aware layout so a single-VM test session that
talks to both upstreams keeps them in separate checkouts.
"""
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from alts.worker.runners.base import GenericVMRunner


GERRIT_QA = 'ssh://gerrit.cloudlinux.com:29418/QA'
GITLAB_QA = 'git@gitlab.corp.cloudlinux.com:clos/ci-tools/QA.git'


def _make_runner(monkeypatch):
    """Return a GenericVMRunner instance with just enough state for
    ``clone_third_party_repo`` to run, without exercising the real
    constructor (which boots terraform/uploaders/etc.).
    """
    runner = GenericVMRunner.__new__(GenericVMRunner)
    runner._tests_dir = '/opt'
    runner._logger = logging.getLogger('test')
    runner._ssh_client = MagicMock()
    # ``is_successful`` is consulted on the return of each SSH call.
    successful = SimpleNamespace(
        is_successful=lambda: True,
        exit_code=0,
        stdout='',
        stderr='',
    )
    runner._ssh_client.sync_run_command.return_value = successful

    # Stub out the parent's clone_third_party_repo so the test doesn't
    # try to do a real local clone — we only care about the VM-side
    # command construction here.
    monkeypatch.setattr(
        'alts.worker.runners.base.BaseRunner.clone_third_party_repo',
        lambda self, repo_url, git_ref: Path('/tmp/fake-local-clone'),
    )
    return runner


class TestVMRepoPathIsHostAware:
    def test_gerrit_qa_uses_gerrit_host_subdir_on_vm(self, monkeypatch):
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GERRIT_QA, 'master')

        # Two SSH commands fired: (1) the if/else clone, (2) fetch+checkout.
        cmds = [
            call.args[0]
            for call in runner._ssh_client.sync_run_command.call_args_list
        ]
        assert len(cmds) >= 1
        expected_repo_path = '/opt/gerrit.cloudlinux.com/QA'
        expected_parent = '/opt/gerrit.cloudlinux.com'
        assert f'if [ -e {expected_repo_path} ]' in cmds[0]
        assert f'mkdir -p {expected_parent}' in cmds[0]
        assert f'git clone {GERRIT_QA} {expected_repo_path}' in cmds[0]

    def test_gitlab_qa_uses_gitlab_path_subdir_on_vm(self, monkeypatch):
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GITLAB_QA, 'master')

        cmds = [
            call.args[0]
            for call in runner._ssh_client.sync_run_command.call_args_list
        ]
        expected_repo_path = (
            '/opt/gitlab.corp.cloudlinux.com/clos/ci-tools/QA'
        )
        expected_parent = '/opt/gitlab.corp.cloudlinux.com/clos/ci-tools'
        assert f'if [ -e {expected_repo_path} ]' in cmds[0]
        assert f'mkdir -p {expected_parent}' in cmds[0]
        assert f'git clone {GITLAB_QA} {expected_repo_path}' in cmds[0]
        # Regression guard: the bare basename path must not appear at all.
        assert '/opt/QA' not in cmds[0]

    def test_two_qa_upstreams_do_not_share_a_directory(self, monkeypatch):
        """A single VM session that clones both upstreams ends up with
        two distinct checkouts. This is the exact failure mode the patch
        fixes: previously both landed at ``/opt/QA`` and the second
        clone's branch wouldn't be visible from the first clone's remote.
        """
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GERRIT_QA, 'master')
        gerrit_cmd = (
            runner._ssh_client.sync_run_command.call_args_list[0].args[0]
        )
        runner._ssh_client.sync_run_command.reset_mock()
        runner.clone_third_party_repo(GITLAB_QA, 'master')
        gitlab_cmd = (
            runner._ssh_client.sync_run_command.call_args_list[0].args[0]
        )

        # Different repo_path tokens appear in each command's if-guard.
        assert (
            'if [ -e /opt/gerrit.cloudlinux.com/QA ]' in gerrit_cmd
        )
        assert (
            'if [ -e /opt/gitlab.corp.cloudlinux.com/clos/ci-tools/QA ]'
            in gitlab_cmd
        )

    def test_second_command_targets_host_aware_path(self, monkeypatch):
        """After clone, the runner runs ``cd <repo_path> && git fetch
        origin && git checkout <ref>``. That path must match the new
        host-aware one, otherwise the wrong checkout would be operated
        on.
        """
        runner = _make_runner(monkeypatch)
        runner.clone_third_party_repo(GITLAB_QA, 'some-branch')
        cmds = [
            call.args[0]
            for call in runner._ssh_client.sync_run_command.call_args_list
        ]
        # Second command is the fetch+checkout.
        assert (
            'cd /opt/gitlab.corp.cloudlinux.com/clos/ci-tools/QA'
            in cmds[1]
        )
        assert 'git fetch origin && git checkout some-branch' in cmds[1]
