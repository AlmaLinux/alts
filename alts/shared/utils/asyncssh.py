import asyncio
import logging
import typing
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import asyncssh


class AsyncSSHClientSession(asyncssh.SSHClientSession):
    def data_received(self, data: str, datatype: asyncssh.DataType):
        if datatype == asyncssh.EXTENDED_DATA_STDERR:
            logging.error(
                'SSH command stderr:\n%s',
                data,
            )
        else:
            logging.info(
                'SSH command stdout:\n%s',
                data,
            )

    def connection_lost(self, exc: typing.Optional[Exception]):
        if exc:
            logging.exception(
                'SSH session error:',
            )
            raise exc


class AsyncSSHClient:
    def __init__(
        self,
        host: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_keys_files: Optional[List[str]] = None,
        known_hosts_files: Optional[List[str]] = None,
        disable_known_hosts_check: bool = False,
        env_vars: Optional[Dict[str, Any]] = None,
    ):
        self.username = username
        self.password = password
        self.host = host
        self.client_keys = client_keys_files
        self.env_vars = env_vars
        self.known_hosts = asyncssh.read_known_hosts(
            ['~/.ssh/known_hosts'] + known_hosts_files
            if known_hosts_files
            else []
        )
        if disable_known_hosts_check:
            self.known_hosts = None

    @asynccontextmanager
    async def get_connection(self):
        async with asyncssh.connect(
            host=self.host,
            username=self.username,
            password=self.password,
            client_keys=self.client_keys,
            known_hosts=self.known_hosts,
            env=self.env_vars,
        ) as conn:
            yield conn

    def sync_run_command(self, command: str):
        try:
            asyncio.run(self.async_run_command(command))
        except Exception as exc:
            logging.exception('Cannot execute asyncssh command: %s', command)
            raise exc

    async def async_run_command(self, command: str):
        async with self.get_connection() as conn:
            channel, session = await conn.create_session(
                AsyncSSHClientSession,
                command,
            )
            await channel.wait_closed()

    async def async_run_commands(self, commands: List[str]):
        async with self.get_connection() as conn:
            for command in commands:
                channel, session = await conn.create_session(
                    AsyncSSHClientSession,
                    command,
                )
                await channel.wait_closed()
