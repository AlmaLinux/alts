import asyncio
import logging
from contextlib import asynccontextmanager
from traceback import format_exc
from typing import Any, Dict, List, Literal, Optional, Union

import asyncssh

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
        preferred_auth: Optional[Union[str, List[str]]] = None,
        disable_known_hosts_check: bool = False,
        ignore_encrypted_keys: bool = False,
        env_vars: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        logger_name: str = 'asyncssh-client',
        logging_level: Literal['DEBUG', 'INFO'] = 'DEBUG',
    ):
        self.username = username
        self.password = password
        self.host = host
        self.timeout = timeout
        self.client_keys = client_keys_files
        self.env_vars = env_vars
        self.ignore_encrypted_keys = ignore_encrypted_keys
        if not preferred_auth:
            preferred_auth = DEFAULT_SSH_AUTH_METHODS
        self.preferred_auth = preferred_auth
        self.known_hosts = asyncssh.read_known_hosts(
            ['~/.ssh/known_hosts'] + known_hosts_files
            if known_hosts_files
            else []
        )
        if disable_known_hosts_check:
            self.known_hosts = None
        if not logger:
            self.logger = self.setup_logger(logger_name, logging_level)

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
        async with asyncssh.connect(
            host=self.host,
            username=self.username,
            password=self.password,
            client_keys=self.client_keys,
            known_hosts=self.known_hosts,
            ignore_encrypted=self.ignore_encrypted_keys,
            preferred_auth=self.preferred_auth,
            env=self.env_vars,
        ) as conn:
            yield conn

    def print_process_results(
        self,
        result: asyncssh.SSHCompletedProcess,
    ):
        self.logger.debug(
            'Exit code: %s, stdout: %s, stderr: %s',
            result.exit_status,
            result.stdout,
            result.stderr,
        )

    async def async_run_command(self, command: str) -> CommandResult:
        async with self.get_connection() as conn:
            result = await conn.run(command, timeout=self.timeout)
            return CommandResult(
                exit_code=result.exit_status,
                stdout=result.stdout,
                stderr=result.stderr,
            )

    def sync_run_command(
        self,
        command: str,
    ) -> CommandResult:
        try:
            result = asyncio.run(self.async_run_command(command))
        except Exception:
            self.logger.exception(
                'Cannot execute asyncssh command: %s',
                command,
            )
            result = CommandResult(
                exit_code=1,
                stdout='',
                stderr=format_exc(),
            )
        return result

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
