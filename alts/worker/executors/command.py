import logging
from typing import Any, Dict, List, Literal, Optional, Union

from alts.shared.models import AsyncSSHParams, CommandResult
from alts.worker.executors.base import BaseExecutor, measure_stage


class CommandExecutor(BaseExecutor):
    def __init__(
        self,
        binary_name: str,
        env_vars: Optional[Dict[str, Any]] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
        timeout: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'command-executor',
        logging_level: Literal['DEBUG', 'INFO'] = 'INFO',
        connection_type: Literal['local', 'ssh', 'docker'] = 'local',
        container_name: str = '',
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
        )

    @measure_stage('run_single_local_command')
    def run_local_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
    ) -> CommandResult:
        return super().run_local_command(cmd_args)

    @measure_stage('run_single_ssh_command')
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

    @measure_stage('run_single_docker_command')
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
