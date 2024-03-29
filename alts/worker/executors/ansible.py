import logging
from typing import Any, Dict, List, Literal, Optional, Union

from alts.shared.models import AsyncSSHParams, CommandResult
from alts.shared.utils.asyncssh import AsyncSSHClient, LongRunSSHClient
from alts.worker.executors.base import BaseExecutor, measure_stage


class AnsibleExecutor(BaseExecutor):
    def __init__(
        self,
        binary_name: str = 'ansible-playbook',
        env_vars: Optional[Dict[str, Any]] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
        ssh_client: Optional[Union[AsyncSSHClient, LongRunSSHClient]] = None,
        timeout: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'ansible-executor',
        logging_level: Literal['DEBUG', 'INFO'] = 'INFO',
        connection_type: Literal['local', 'ssh', 'docker'] = 'local',
        container_name: str = '',
        check_binary_existence: bool = False,
    ):
        super().__init__(
            binary_name=binary_name,
            env_vars=env_vars,
            ssh_params=ssh_params,
            ssh_client=ssh_client,
            timeout=timeout,
            logger=logger,
            logger_name=logger_name,
            logging_level=logging_level,
            connection_type=connection_type,
            container_name=container_name,
            check_binary_existence=check_binary_existence,
        )
        self._ansible_host = 'localhost'
        self._ansible_user = 'root'
        if ssh_params:
            if isinstance(ssh_params, AsyncSSHParams):
                self._ansible_host = ssh_params.host
                self._ansible_user = ssh_params.username or 'root'
            else:
                self._ansible_host = ssh_params['host']
                self._ansible_user = ssh_params.get('username') or 'root'
        if ssh_client:
            self._ansible_host = ssh_client.host
            self._ansible_user = ssh_client.username or 'root'

    def __construct_cmd_args(
        self,
        cmd_args: List[str],
        env_vars: Optional[List[str]] = None,
    ) -> List[str]:
        args = [
            '-i',
            f'{self._ansible_host},',
            '-u',
            self._ansible_user,
            '-c',
            self.connection_type,
        ]
        env_vars_parts = [f'{k}={v}' for k, v in self.env_vars.items()]
        if env_vars:
            env_vars_parts.extend(env_vars)
        if env_vars_parts:
            args += ['-e', ' '.join(env_vars_parts)]
        args += cmd_args
        return args

    @measure_stage('run_local_ansible')
    def run_local_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        args = self.__construct_cmd_args(cmd_args, env_vars=env_vars)
        return super().run_local_command(args, workdir=workdir)

    @measure_stage('run_remote_ansible')
    def run_ssh_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        self.check_binary_existence()
        args = self.__construct_cmd_args(cmd_args)
        return super().run_ssh_command(
            args,
            workdir=workdir,
            env_vars=env_vars,
        )

    @measure_stage('run_docker_ansible')
    def run_docker_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        docker_args: Optional[List[str]] = None,
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        self.check_binary_existence()
        args = self.__construct_cmd_args(cmd_args)
        return super().run_docker_command(
            cmd_args=args,
            docker_args=docker_args,
            env_vars=env_vars,
        )

    def run(
        self,
        cmd_args: List[str],
        workdir: str = '',
        **kwargs,
    ):
        return self.run_local_command(cmd_args, workdir, **kwargs)
