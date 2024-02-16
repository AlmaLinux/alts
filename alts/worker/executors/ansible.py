import logging
from typing import Any, Dict, List, Literal, Optional, Union

from alts.shared.models import AsyncSSHParams, CommandResult
from alts.worker.executors.base import BaseExecutor, measure_stage


class AnsibleExecutor(BaseExecutor):
    def __init__(
        self,
        binary_name: str = 'ansible-playbook',
        env_vars: Optional[Dict[str, Any]] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
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

    def __construct_cmd_args(self, cmd_args: List[str]) -> List[str]:
        args = ['-i', f'{self._ansible_host},', '-u', self._ansible_user]
        if self.env_vars:
            args += ['-e', f'{self.env_vars}']
        args += cmd_args
        return args

    @measure_stage('run_local_ansible')
    def run_local_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
    ) -> CommandResult:
        args = self.__construct_cmd_args(cmd_args)
        return super().run_local_command(args)

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
