import logging
from typing import Any, Dict, List, Literal, Optional, Union

from alts.shared.models import AsyncSSHParams, CommandResult
from alts.shared.utils.asyncssh import AsyncSSHClient, LongRunSSHClient
from alts.worker.executors.base import BaseExecutor, measure_stage


class ShellExecutor(BaseExecutor):
    def __init__(
        self,
        binary_name: str = 'bash',
        env_vars: Optional[Dict[str, Any]] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
        ssh_client: Optional[Union[AsyncSSHClient, LongRunSSHClient]] = None,
        timeout: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'shell-executor',
        logging_level: Literal['DEBUG', 'INFO'] = 'INFO',
        connection_type: Literal['local', 'ssh', 'docker'] = 'local',
        container_name: str = '',
        check_binary_existence: bool = True,
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

    @measure_stage('run_local_script')
    def run_local_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        return super().run_local_command(
            cmd_args,
            workdir=workdir,
            env_vars=env_vars,
        )

    @measure_stage('run_ssh_script')
    def run_ssh_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        return super().run_ssh_command(
            cmd_args,
            workdir=workdir,
            env_vars=env_vars,
        )

    @measure_stage('run_docker_script')
    def run_docker_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        docker_args: Optional[List[str]] = None,
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        return super().run_docker_command(
            cmd_args=cmd_args,
            docker_args=docker_args,
            env_vars=env_vars,
        )
