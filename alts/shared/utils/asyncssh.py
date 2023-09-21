import asyncio
import logging
from contextlib import asynccontextmanager
from traceback import format_exc
from typing import Any, Dict, List, Literal, Optional, Union

from asyncssh import (
    SSHCompletedProcess,
    SSHKey,
    connect,
    read_known_hosts,
    read_public_key,
)

from alts.shared.constants import DEFAULT_SSH_AUTH_METHODS
from alts.shared.models import CommandResult


class AsyncSSHClient:
    def __init__(
        self,
        host: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
        client_keys_files: Optional[List[str]] = None,
        known_hosts_files: Optional[List[str]] = None,
        env_vars: Optional[Dict[str, Any]] = None,
        disable_known_hosts_check: bool = False,
        ignore_encrypted_keys: bool = False,
        keepalive_interval: int = 0,
        keepalive_count_max: int = 3,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'asyncssh-client',
        logging_level: Literal['DEBUG', 'INFO'] = 'DEBUG',
        preferred_auth: Optional[Union[str, List[str]]] = None,
    ):
        self.username = username
        self.password = password
        self.host = host
        self.timeout = timeout
        self.env_vars = env_vars
        self.ignore_encrypted_keys = ignore_encrypted_keys
        self.client_keys = self.read_client_keys(client_keys_files)
        self.preferred_auth = (
            preferred_auth if preferred_auth else DEFAULT_SSH_AUTH_METHODS
        )
        self.keepalive_interval = keepalive_interval
        self.keepalive_count_max = keepalive_count_max
        known_hosts = (
            known_hosts_files if known_hosts_files else []
        )
        self.known_hosts = read_known_hosts(known_hosts)
        if disable_known_hosts_check:
            self.known_hosts = None
        if not logger:
            self.logger = self.setup_logger(logger_name, logging_level)

    def read_client_keys(
        self,
        filenames: Optional[List[str]],
    ) -> List[SSHKey]:
        client_keys = []
        if not filenames:
            return client_keys
        for filename in filenames:
            client_keys.append(read_public_key(filename))
        return client_keys

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

    @asynccontextmanager
    async def get_connection(self):
        async with connect(
            host=self.host,
            username=self.username,
            password=self.password,
            client_host_keys=self.client_keys,
            known_hosts=self.known_hosts,
            preferred_auth=self.preferred_auth,
            env=self.env_vars,
            ignore_encrypted=self.ignore_encrypted_keys,
            keepalive_interval=self.keepalive_interval,
            keepalive_count_max=self.keepalive_count_max,
        ) as conn:
            yield conn

    def print_process_results(
        self,
        result: SSHCompletedProcess,
    ):
        self.logger.debug(
            'Exit code: %s, stdout: %s, stderr: %s',
            result.exit_status,
            result.stdout,
            result.stderr,
        )

    async def async_run_command(self, command: str) -> CommandResult:
        async with self.get_connection() as conn:
            try:
                result = await conn.run(command, timeout=self.timeout)
                exit_code, stdout, stderr = (
                    result.exit_status,
                    result.stdout,
                    result.stderr,
                )
            except Exception:
                self.logger.exception('Cannot execute SSH command:')
                exit_code, stdout, stderr = 1, '', format_exc()
            return CommandResult(
                exit_code=1 if exit_code is None else exit_code,
                stdout=stdout,
                stderr=stderr,
            )

    def sync_run_command(
        self,
        command: str,
    ) -> CommandResult:
        return asyncio.run(self.async_run_command(command))

    async def async_run_commands(
        self,
        commands: List[str],
    ) -> Dict[str, CommandResult]:
        results = {}
        async with self.get_connection() as conn:
            for command in commands:
                try:
                    result = await conn.run(command, timeout=self.timeout)
                except Exception:
                    self.logger.exception(
                        'Cannot execute asyncssh command: %s',
                        command,
                    )
                    results[command] = CommandResult(
                        exit_code=1,
                        stdout='',
                        stderr=format_exc(),
                    )
                    continue
                self.print_process_results(result)
                results[command] = CommandResult(
                    exit_code=result.exit_status,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        return results

    def sync_run_commands(
        self,
        commands: List[str],
    ) -> Dict[str, CommandResult]:
        try:
            return asyncio.run(self.async_run_commands(commands))
        except Exception as exc:
            self.logger.exception(
                'Cannot execute asyncssh commands: %s', commands
            )
            raise exc
