import logging
from datetime import datetime
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple, Union

from plumbum import local

from alts.shared.models import AsyncSSHParams
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
        env_vars: Optional[Dict[str, Any]] = None,
        binary_name: Optional[str] = None,
        ssh_params: Optional[Union[Dict[str, Any], AsyncSSHParams]] = None,
        timeout: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
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
            self.ssh_client = AsyncSSHClient(**ssh_params.dict())
        self.logger = logger
        if not self.logger:
            self.logger = logging.getLogger('executor')

    @measure_stage('run_local_command')
    def run_local_command(self, cmd_args: List[str]) -> Tuple[int, str, str]:
        if self.binary_name not in local:
            raise FileNotFoundError(
                f'Binary {self.binary_name} is not found in PATH on the machine',
            )
        with local.env(**self.env_vars):
            return local[self.binary_name].run(
                args=cmd_args,
                timeout=self.timeout,
            )

    @measure_stage('run_ssh_command')
    def run_ssh_command(self, cmd: str):
        if not self.ssh_client:
            raise ValueError('SSH params are missing')
        return self.ssh_client.sync_run_command(cmd)
