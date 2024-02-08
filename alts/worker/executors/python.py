import logging
import os.path
import re
from typing import Any, Dict, List, Literal, Optional, Union, Tuple

from alts.shared.models import AsyncSSHParams, CommandResult
from alts.worker.executors.base import BaseExecutor, measure_stage

INTERPRETER_REGEX = re.compile(
    r'^#!(?P<python_interpreter>.*(python[2-4]?))(?P<options> .*)?'
)


class PythonExecutor(BaseExecutor):
    def __init__(
        self,
        binary_name: str = 'python',
        env_vars: Optional[Dict[str, Any]] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
        timeout: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'python-executor',
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

    def check_binary_existence(self):
        try:
            super().check_binary_existence()
        except FileNotFoundError:
            self.binary_name = 'python3'
            super().check_binary_existence()

    def detect_python_binary(
        self,
        cmd_args: List[str],
        workdir: str,
    ) -> Tuple[str, str]:
        if '--version' in cmd_args:
            return self.binary_name, ''
        if not cmd_args:
            return self.binary_name, ''
        script_name = cmd_args[0]
        with open(os.path.join(workdir, script_name), 'rt') as f:
            shebang = f.readline()
            result = INTERPRETER_REGEX.search(shebang)
            if not result:
                return self.binary_name, ''
            result_dict = result.groupdict()
            if 'python_interpreter' not in result_dict:
                return self.binary_name, ''
            interpreter = result_dict['python_interpreter']
            options = ''
            if 'options' in result_dict:
                options = result_dict['options'].strip()
            return interpreter, options

    @measure_stage('run_local_python')
    def run_local_command(
        self,
        cmd_args: List[str],
        workdir: str = '',
    ) -> CommandResult:
        interpreter, options = self.detect_python_binary(cmd_args, workdir)
        self.binary_name = interpreter
        if options:
            cmd_args.insert(0, options)
        return super().run_local_command(cmd_args)

    @measure_stage('run_ssh_python')
    def run_ssh_command(
            self,
            cmd_args: List[str],
            workdir: str = '',
            env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        interpreter, options = self.detect_python_binary(cmd_args, workdir)
        self.binary_name = interpreter
        if options:
            cmd_args.insert(0, options)
        return super().run_ssh_command(
            cmd_args,
            workdir=workdir,
            env_vars=env_vars,
        )

    @measure_stage('run_docker_python')
    def run_docker_command(
            self,
            cmd_args: List[str],
            workdir: str = '',
            docker_args: Optional[List[str]] = None,
            env_vars: Optional[List[str]] = None,
    ) -> CommandResult:
        interpreter, options = self.detect_python_binary(cmd_args, workdir)
        self.binary_name = interpreter
        if options:
            cmd_args.insert(0, options)
        return super().run_docker_command(
            cmd_args=cmd_args,
            docker_args=docker_args,
            env_vars=env_vars,
        )
