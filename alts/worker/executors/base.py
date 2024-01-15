import logging
from datetime import datetime
from functools import wraps
from traceback import format_exc
from typing import Any, Dict, List, Literal, Optional, Union

from plumbum import local

from alts.shared.models import AsyncSSHParams, CommandResult
from alts.shared.utils.asyncssh import AsyncSSHClient


def measure_stage(stage: str):
    def wrapper(func):
        @wraps(func)
        def wrapped(self, *args, **kwargs):
            start_time = datetime.utcnow()
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                raise exc
            finally:
                end_time = datetime.utcnow()
                self.exec_stats[stage] = {
                    'start_ts': start_time.isoformat(),
                    'end_ts': end_time.isoformat(),
                    'delta': (end_time - start_time).total_seconds(),
                }

        return wrapped

    return wrapper


class BaseExecutor:
    def __init__(
        self,
        binary_name: str,
        env_vars: Optional[Dict[str, Any]] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
        timeout: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'base-executor',
        logging_level: Literal['DEBUG', 'INFO'] = 'DEBUG',
        connection_type: Literal['local', 'ssh', 'docker'] = 'local',
        container_name: str = '',
    ) -> None:
        self.ssh_client = None
        self.env_vars = {}
        self.exec_stats = {}
        self.timeout = timeout
        self.binary_name = binary_name
        if self.timeout is None:
            self.timeout = 30
        if env_vars and isinstance(env_vars, dict):
            self.env_vars.update(env_vars)
        if ssh_params:
            if isinstance(ssh_params, dict):
                ssh_params['env_vars'] = env_vars if env_vars else None
                ssh_params = AsyncSSHParams(**ssh_params)
            self.ssh_client = AsyncSSHClient(**ssh_params.model_dump())
        self.connection_type = connection_type
        self.container_name = container_name
        self.logger = logger
        if not self.logger:
            self.logger = self.setup_logger(logger_name, logging_level)
        self.check_binary_existence()

    def setup_logger(
        self,
        logger_name: str,
        logging_level: str,
    ) -> logging.Logger:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging_level)
        handler = logging.StreamHandler()
        handler.setLevel(logging_level)
        formatter = logging.Formatter(
            '%(asctime)s [%(name)s:%(levelname)s] - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def check_binary_existence(self):
        func = self.run_local_command
        if self.ssh_client:
            func = self.run_ssh_command
        if self.connection_type == 'docker':
            func = self.run_docker_command
        try:
            result = func(['--version'])
        except Exception as exc:
            self.logger.exception('Cannot check binary existence:')
            raise exc
        if not result.is_successful():
            raise FileNotFoundError(
                f'Binary "{self.binary_name}" is not found in PATH '
                f'on the machine\n{result.stderr}',
            )

    @measure_stage('run_local_command')
    def run_local_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
    ) -> CommandResult:
        try:
            executable = local[self.binary_name].with_env(**self.env_vars)
            if workdir:
                executable = executable.with_cwd(workdir)
            exit_code, stdout, stderr = executable.run(
                args=cmd_args,
                timeout=self.timeout,
            )
        except Exception:
            self.logger.exception('Cannot run local command:')
            exit_code, stdout, stderr = 1, '', format_exc()
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    @measure_stage('run_ssh_command')
    def run_ssh_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        if not self.ssh_client:
            raise ValueError('SSH params are missing')
        directory = f'cd {workdir} && ' if workdir else ''
        additional_env_vars = f"{' '.join(env_vars)} " if env_vars else ''
        return self.ssh_client.sync_run_command(
            directory
            + additional_env_vars
            + ' '.join([self.binary_name, *cmd_args])
        )

    @measure_stage('run_docker_command')
    def run_docker_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
        docker_args: Optional[List[str]] = None,
        env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        docker_args = docker_args if docker_args else []
        additional_env_vars = []
        if env_vars:
            for var in env_vars:
                additional_env_vars.extend(('-e', var))
        try:
            exit_code, stdout, stderr = (
                local['docker']
                .with_env(**self.env_vars)
                .run(
                    args=[
                        'exec',
                        *docker_args,
                        *additional_env_vars,
                        self.container_name,
                        self.binary_name,
                        *cmd_args,
                    ],
                    timeout=self.timeout,
                    retcode=None,
                )
            )
        except Exception:
            self.logger.exception('Cannot run docker command:')
            exit_code, stdout, stderr = 1, '', format_exc()
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def run(
        self,
        cmd_args: List[str],
        workdir: str = '',
        **kwargs,
    ) -> CommandResult:
        executable = self.run_local_command
        if self.connection_type == 'ssh':
            executable = self.run_ssh_command
        elif self.connection_type == 'docker':
            executable = self.run_docker_command
        return executable(cmd_args, workdir=workdir, **kwargs)
