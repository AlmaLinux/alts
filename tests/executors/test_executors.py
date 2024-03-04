from contextlib import nullcontext as does_not_raise
from typing import Any, Dict, List

import pytest
from asyncssh.misc import PermissionDenied, HostKeyNotVerifiable

from alts.worker.executors.ansible import AnsibleExecutor
from alts.worker.executors.base import BaseExecutor
from alts.worker.executors.bats import BatsExecutor
from alts.worker.executors.command import CommandExecutor
from alts.worker.executors.shell import ShellExecutor


class TestBaseExecutor:
    @pytest.mark.parametrize(
        'binary_name, ssh_params, exception',
        [
            pytest.param(
                'bash',
                {},
                does_not_raise(),
                id='bash',
            ),
            pytest.param(
                'foo_bar',
                {},
                pytest.raises(FileNotFoundError),
                id='unexsistent_binary',
            ),
            pytest.param(
                'bash',
                {'disable_known_hosts_check': True},
                does_not_raise(),
                id='on_remote',
            ),
            pytest.param(
                'foo_bar',
                {'disable_known_hosts_check': True},
                pytest.raises(FileNotFoundError),
                id='unexsistent_binary_on_remote',
            ),
        ],
    )
    def test_base_executor_init(
        self,
        local_ssh_credentials: Dict[str, Any],
        binary_name: str,
        ssh_params: Dict[str, Any],
        exception,
    ):
        executor_params = {'binary_name': binary_name}
        if ssh_params:
            executor_params['ssh_params'] = {
                **local_ssh_credentials,
                **ssh_params,
            }
        with exception:
            executor = BaseExecutor(**executor_params)
            assert executor.check_binary_existence() is None

    @pytest.mark.parametrize(
        'executor_params, cmd_args, expected_exit_code',
        [
            pytest.param(
                {'binary_name': 'bash'},
                ['--version'],
                0,
                id='bash',
            ),
            pytest.param(
                {'binary_name': 'sleep', 'timeout': 1},
                [5],
                1,
                id='local_timeout',
            ),
        ],
    )
    def test_base_executor_run_local_command(
        self,
        executor_params: Dict[str, Any],
        cmd_args: List[str],
        expected_exit_code: int,
    ):
        executor = BaseExecutor(**executor_params)
        result = executor.run_local_command(cmd_args)
        assert result.is_successful(expected_exit_code=expected_exit_code)

    @pytest.mark.parametrize(
        'executor_params, additional_ssh_params, command, exception, expected_exit_code',
        [
            pytest.param(
                {'binary_name': 'bash'},
                {'disable_known_hosts_check': True},
                ['--version'],
                does_not_raise(),
                0,
                id='bash',
            ),
            pytest.param(
                {'binary_name': 'bash', 'check_binary_existence': False},
                {},
                ['--version'],
                pytest.raises(HostKeyNotVerifiable),
                1,
                id='untrusted_host_key',
            ),
            pytest.param(
                {'binary_name': 'bash', 'check_binary_existence': False},
                {
                    'password': 'foo_bar',
                    'client_keys_files': [],
                    'preferred_auth': 'password',
                    'disable_known_hosts_check': True,
                },
                ['--version'],
                pytest.raises(PermissionDenied),
                1,
                id='permission_denied',
            ),
            pytest.param(
                {'binary_name': 'sleep', 'timeout': 5},
                {'disable_known_hosts_check': True},
                ['10', '&&', 'echo "check"'],
                does_not_raise(),
                1,
                id='ssh_timeout',
            ),
        ],
    )
    def test_run_ssh_command(
        self,
        local_ssh_credentials: Dict[str, str],
        executor_params: Dict[str, Any],
        additional_ssh_params: Dict[str, Any],
        command: List[str],
        expected_exit_code: int,
        exception,
    ):
        ssh_params = {
            **local_ssh_credentials,
            **additional_ssh_params,
        }
        executor_params['ssh_params'] = ssh_params
        with exception:
            executor = BaseExecutor(**executor_params)
            result = executor.run_ssh_command(command)
            assert result.is_successful(expected_exit_code=expected_exit_code)


class TestBatsExecutor:
    @pytest.mark.parametrize(
        'binary_name, ssh_params, exception',
        [
            pytest.param(
                'bats',
                {},
                does_not_raise(),
                id='bats',
            ),
            pytest.param(
                'foo_bar',
                {},
                pytest.raises(FileNotFoundError),
                id='unexsistent_binary',
            ),
            pytest.param(
                'bats',
                {'disable_known_hosts_check': True},
                does_not_raise(),
                id='on_remote',
            ),
            pytest.param(
                'foo_bar',
                {'disable_known_hosts_check': True},
                pytest.raises(FileNotFoundError),
                id='unexsistent_binary_on_remote',
            ),
        ],
    )
    def test_base_executor_init(
        self,
        local_ssh_credentials: Dict[str, Any],
        binary_name: str,
        ssh_params: Dict[str, Any],
        exception,
    ):
        executor_params = {}
        if binary_name:
            executor_params['binary_name'] = binary_name
        if ssh_params:
            executor_params['ssh_params'] = {
                **local_ssh_credentials,
                **ssh_params,
            }
        with exception:
            executor = BatsExecutor(**executor_params)
            assert executor.check_binary_existence() is None

    @pytest.mark.parametrize(
        'bats_file_path, ssh_params, expected_exit_code, exception',
        [
            pytest.param(
                '',
                {},
                0,
                does_not_raise(),
                id='local',
            ),
            pytest.param(
                '',
                {'disable_known_hosts_check': True},
                0,
                does_not_raise(),
                id='on_remote',
            ),
            pytest.param(
                'foo_bar.bats',
                {},
                1,
                does_not_raise(),
                id='local_unexistent_test',
            ),
            pytest.param(
                'foo_bar.bats',
                {'disable_known_hosts_check': True},
                1,
                does_not_raise(),
                id='remote_unexistent_test',
            ),
        ],
    )
    def test_run_bats_test(
        self,
        bats_file_path: str,
        simple_bats_file: str,
        local_ssh_credentials: Dict[str, Any],
        ssh_params: Dict[str, Any],
        expected_exit_code: int,
        exception,
    ):
        executor_params = {}
        cmd_args = [bats_file_path or simple_bats_file]
        run_method = 'run_local_command'
        if ssh_params:
            executor_params['ssh_params'] = {
                **local_ssh_credentials,
                **ssh_params,
            }
            run_method = 'run_ssh_command'
        executor = BatsExecutor(**executor_params)
        with exception:
            result = getattr(executor, run_method)(cmd_args)
            assert result.is_successful(expected_exit_code=expected_exit_code)


class TestShellExecutor:
    def test_shell_executor_init(self):
        assert isinstance(ShellExecutor(), ShellExecutor)

    @pytest.mark.parametrize(
        'executor_params, additional_ssh_params',
        [
            pytest.param(
                {},
                {},
                id='local',
            ),
            pytest.param(
                {},
                {'disable_known_hosts_check': True},
                id='on_remote',
            ),
        ],
    )
    def test_shell_executor_run_command(
        self,
        simple_shell_script: str,
        local_ssh_credentials: Dict[str, Any],
        executor_params: Dict[str, Any],
        additional_ssh_params: Dict[str, Any],
    ):
        func = 'run_local_command'
        cmd_args = [simple_shell_script]
        if additional_ssh_params:
            executor_params['ssh_params'] = {
                **local_ssh_credentials,
                **additional_ssh_params,
            }
            func = 'run_ssh_command'
        executor = ShellExecutor(**executor_params)
        result = getattr(executor, func)(cmd_args)
        assert result.is_successful()


class TestAnsibleExecutor:
    def test_ansible_executor_init(self):
        assert isinstance(AnsibleExecutor(), AnsibleExecutor)

    # TODO: Right now we do not expect Ansible to be started
    #  on the remote machine, so commenting remote fixtures for now
    @pytest.mark.parametrize(
        'executor_params, additional_ssh_params, extra_cmd_args',
        [
            pytest.param(
                {},
                {},
                [],
                id='local',
            ),
            # pytest.param(
            #     {},
            #     {'disable_known_hosts_check': True},
            #     [],
            #     id='on_remote',
            # ),
            pytest.param(
                {},
                {},
                ['--list-tasks', '--syntax-check'],
                id='local_with_extra_params',
            ),
            # pytest.param(
            #     {},
            #     {'disable_known_hosts_check': True},
            #     ['--list-tasks', '--syntax-check'],
            #     id='on_remote_with_extra_params',
            # ),
        ],
    )
    def test_ansible_run_command(
        self,
        executor_params: Dict[str, Any],
        additional_ssh_params: Dict[str, Any],
        extra_cmd_args: List[str],
        local_ssh_credentials: Dict[str, Any],
        simple_ansible_playbook: str,
    ):
        cmd_args = [*extra_cmd_args, simple_ansible_playbook]
        func = 'run_local_command'
        if additional_ssh_params:
            executor_params['ssh_params'] = {
                **local_ssh_credentials,
                **additional_ssh_params,
            }
            func = 'run_ssh_command'
        executor = AnsibleExecutor(**executor_params)
        result = getattr(executor, func)(cmd_args)
        assert result.is_successful()


class TestCommandExecutor:
    def test_command_executor_init(self):
        assert isinstance(CommandExecutor(binary_name='bash'), CommandExecutor)

    @pytest.mark.parametrize(
        'cmd_args, additional_ssh_params',
        [
            pytest.param(
                ['--version'],
                {},
                id='local',
            ),
            pytest.param(
                ['--version'],
                {'disable_known_hosts_check': True},
                id='on_remote',
            ),
        ],
    )
    def test_command_executor_run_command(
        self,
        cmd_args: List[str],
        additional_ssh_params: Dict[str, Any],
        local_ssh_credentials: Dict[str, Any],
    ):
        executor_params = {'binary_name': 'bash'}
        func = 'run_local_command'
        if additional_ssh_params:
            executor_params['ssh_params'] = {
                **local_ssh_credentials,
                **additional_ssh_params,
            }
            func = 'run_ssh_command'
        executor = CommandExecutor(**executor_params)
        result = getattr(executor, func)(cmd_args)
        assert result.is_successful()
