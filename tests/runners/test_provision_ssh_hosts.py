"""`initial_provision` passes the configured third-party SSH hosts to
Ansible as an extra-var, so the preparation role can render the VM's
`/root/.ssh/config`.

The value comes from `CONFIG.third_party_repo_ssh_hosts` and is
serialized with `exclude_none=True` (so host-only entries don't carry
an empty `user`).
"""
import ast
import logging
import os
import re

import pytest

from alts.worker import runners
from alts.worker.runners.base import GenericVMRunner
from alts.shared.models import CachedTestRepo, ThirdPartyRepoSshHost


def _make_runner(tmp_path):
    runner = GenericVMRunner.__new__(GenericVMRunner)
    runner._work_dir = str(tmp_path)
    runner._artifacts = {}
    runner._stats = {}
    runner.already_aborted = True  # skip the abort check in the decorator
    runner._logger = logging.getLogger('test')
    runner._secrets = []
    runner._repositories = []
    runner._integrity_tests_dir = str(tmp_path / 'tests')
    runner._ansible_connection_type = 'ssh'
    runner._test_env = {'pytest_is_needed': False}
    runner._dist_name = 'almalinux'
    runner._dist_version = '9'
    runner._env_name = 'env-test'
    return runner


def _extract_extra_vars(cmd_args):
    """Read the `-e @<file>` payload referenced by the ansible args and
    parse it back into a Python dict.
    """
    idx = cmd_args.index('-e')
    vars_arg = cmd_args[idx + 1]
    assert vars_arg.startswith('@')
    with open(vars_arg[1:]) as vars_file:
        return ast.literal_eval(vars_file.read())


class TestInitialProvisionRepositories:
    def test_credentials_are_not_in_command_line(self, tmp_path):
        url = 'https://alts:s3cr3t-t0ken@build.example.com/repo/'
        captured = {}

        def fake_run(cmd_args, timeout=None):
            captured['args'] = cmd_args
            return 0, '', ''

        runner = _make_runner(tmp_path)
        runner._repositories = [{'name': 'al-9-x86_64-1-br', 'url': url}]
        runner.run_ansible_command = fake_run

        runner.initial_provision()

        assert not any('s3cr3t-t0ken' in str(arg) for arg in captured['args'])
        vars_path = captured['args'][captured['args'].index('-e') + 1][1:]
        assert os.stat(vars_path).st_mode & 0o777 == 0o600
        extra_vars = _extract_extra_vars(captured['args'])
        assert extra_vars['repositories'] == [
            {'name': 'al-9-x86_64-1-br', 'url': url},
        ]


class TestInitialProvisionSshHosts:
    def test_extra_vars_include_configured_hosts(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            runners.base.CONFIG,
            'third_party_repo_ssh_hosts',
            [
                ThirdPartyRepoSshHost(
                    host='gerrit.cloudlinux.com',
                    user='alternatives',
                ),
                ThirdPartyRepoSshHost(host='gitlab.corp.cloudlinux.com'),
            ],
            raising=False,
        )
        captured = {}

        def fake_run(cmd_args, timeout=None):
            captured['args'] = cmd_args
            return 0, '', ''

        runner = _make_runner(tmp_path)
        runner.run_ansible_command = fake_run

        runner.initial_provision()

        extra_vars = _extract_extra_vars(captured['args'])
        assert extra_vars['third_party_repo_ssh_hosts'] == [
            {'host': 'gerrit.cloudlinux.com', 'user': 'alternatives'},
            {'host': 'gitlab.corp.cloudlinux.com'},  # no empty `user`
        ]

    def test_extra_vars_empty_when_unconfigured(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            runners.base.CONFIG,
            'third_party_repo_ssh_hosts',
            [],
            raising=False,
        )
        captured = {}

        def fake_run(cmd_args, timeout=None):
            captured['args'] = cmd_args
            return 0, '', ''

        runner = _make_runner(tmp_path)
        runner.run_ansible_command = fake_run

        runner.initial_provision()

        extra_vars = _extract_extra_vars(captured['args'])
        assert extra_vars['third_party_repo_ssh_hosts'] == []


class TestInitialProvisionCachedTestRepos:
    def test_extra_vars_include_configured_caches(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            runners.base.CONFIG,
            'cached_test_repos',
            [
                CachedTestRepo(
                    src='/opt/QA',
                    dest='/opt/gerrit.cloudlinux.com/QA',
                ),
            ],
            raising=False,
        )
        captured = {}

        def fake_run(cmd_args, timeout=None):
            captured['args'] = cmd_args
            return 0, '', ''

        runner = _make_runner(tmp_path)
        runner.run_ansible_command = fake_run

        runner.initial_provision()

        extra_vars = _extract_extra_vars(captured['args'])
        assert extra_vars['cached_test_repos'] == [
            {'src': '/opt/QA', 'dest': '/opt/gerrit.cloudlinux.com/QA'},
        ]

    def test_extra_vars_empty_when_unconfigured(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            runners.base.CONFIG,
            'cached_test_repos',
            [],
            raising=False,
        )
        captured = {}

        def fake_run(cmd_args, timeout=None):
            captured['args'] = cmd_args
            return 0, '', ''

        runner = _make_runner(tmp_path)
        runner.run_ansible_command = fake_run

        runner.initial_provision()

        extra_vars = _extract_extra_vars(captured['args'])
        assert extra_vars['cached_test_repos'] == []
