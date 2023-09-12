from contextlib import nullcontext as does_not_raise
from typing import Any, Dict, List

import pytest
from asyncssh.misc import HostKeyNotVerifiable, PermissionDenied

from alts.worker.executors.base import BaseExecutor
from alts.worker.executors.bats import BatsExecutor


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
        'binary_name, cmd_args, expected_exit_code',
        [
            pytest.param(
                'bash',
                ['--version'],
                0,
                id='bash',
            ),
            pytest.param(
                'man',
                ['--version'],
                0,
                id='man',
            ),
        ],
    )
    def test_base_executor_run_local_command(
        self,
        binary_name: str,
        cmd_args: List[str],
        expected_exit_code: int,
    ):
        executor = BaseExecutor(binary_name=binary_name)
        result = executor.run_local_command(cmd_args)
        assert result.is_successful(expected_exit_code=expected_exit_code)

    @pytest.mark.parametrize(
        'binary_name, additional_ssh_params, exception',
        [
            pytest.param(
                'bash',
                {'disable_known_hosts_check': True},
                does_not_raise(),
                id='bash',
            ),
            pytest.param(
                'bash',
                {},
                pytest.raises(HostKeyNotVerifiable),
                id='untrusted_host_key',
            ),
            pytest.param(
                'bash',
                {
                    'password': 'foo_bar',
                    'client_keys_files': [],
                    'preferred_auth': 'password',
                    'disable_known_hosts_check': True,
                },
                pytest.raises(PermissionDenied),
                id='permission_denied',
            ),
        ],
    )
    def test_run_ssh_command(
        self,
        local_ssh_credentials: Dict[str, str],
        binary_name: str,
        additional_ssh_params: Dict[str, Any],
        exception,
    ):
        ssh_params = {
            **local_ssh_credentials,
            **additional_ssh_params,
        }
        with exception:
            executor = BaseExecutor(
                binary_name=binary_name,
                ssh_params=ssh_params,
            )
            result = executor.run_ssh_command('--version')
            assert result.is_successful()


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
            cmd_args = bats_file_path or simple_bats_file
            run_method = 'run_ssh_command'
        executor = BatsExecutor(**executor_params)
        with exception:
            result = getattr(executor, run_method)(cmd_args)
            assert result.is_successful(expected_exit_code=expected_exit_code)
