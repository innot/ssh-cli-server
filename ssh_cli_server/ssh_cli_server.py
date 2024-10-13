#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import logging
from asyncio import Event
from typing import Callable, Optional, Coroutine, Any, Type, TypeAlias, Union

import asyncssh
from prompt_toolkit.contrib.ssh import PromptToolkitSSHSession

from ssh_cli_server.abstract_cli import AbstractCLI, AbstractCLIFactory
from ssh_cli_server.connection_info import ConnectionInfo
from ssh_cli_server.serverconfig import ServerConfig

logger = logging.getLogger(__name__)

InteractFunction: TypeAlias = Callable[[PromptToolkitSSHSession], Coroutine[Any, Any, None]]
CLI_Handler: TypeAlias = Union[Type[AbstractCLI], AbstractCLI, AbstractCLIFactory, InteractFunction]


class SSHCLIPromptToolkitSession(PromptToolkitSSHSession):

    def __init__(self,
                 interact: InteractFunction,
                 conn_info: ConnectionInfo) -> None:
        super().__init__(interact, enable_cpr=True)
        self.connection_info = conn_info


class _InternalSSHConnection(asyncssh.SSHServer):
    """This class is created for and handles each single ssh client connection.

        If the connection is successfull a new :class:`SSHCLIPromptToolkitSession` session is
        created and started.

    :param cli:
    """

    def __init__(
            self,
            cli: CLI_Handler,
            server: SSHCLIServer,
            config: ServerConfig = None
    ) -> None:

        self._conn = None
        self._cli_handler = cli
        if config:
            self._config = config
        else:
            self._config = ServerConfig()  # use default

        self.conn_info = ConnectionInfo()
        """Stores information about this session."""

        self.conn_info.sshserver = server
        self.conn_info.session = self

    def connection_made(self, connection: asyncssh.SSHServerConnection) -> None:
        # store information that might be useful
        self.conn_info.connection = connection
        remote = connection.get_extra_info("peername")
        self.conn_info.remote_addr = ipaddress.ip_address(remote[0])
        self.conn_info.remote_port = remote[1]
        self.conn_info.username = connection.get_extra_info("username")

    def session_requested(self) -> PromptToolkitSSHSession:
        if isinstance(self._cli_handler, AbstractCLIFactory):
            # create a new cli for this session
            cli = self._cli_handler.cli()
        elif isinstance(self._cli_handler, AbstractCLI):
            # Use the already existing CLI object
            cli = self._cli_handler
        elif inspect.isclass(self._cli_handler):
            # Instantiate a new CLI object
            cli = self._cli_handler()
        else:
            # cli is just a function or method:
            # wrap inside a class
            interact_method: InteractFunction = self._cli_handler

            class _Cli(AbstractCLI):
                async def interact(self, ssh_session: PromptToolkitSSHSession):
                    await interact_method(ssh_session)

            cli = _Cli()

        session = SSHCLIPromptToolkitSession(cli.interact, self.conn_info)
        cli.connection_info = self.conn_info
        return session

    def password_auth_supported(self) -> bool:
        return self._config.enable_passwords

    def validate_password(self, username: str, password: str) -> bool:
        return self._config.passwordmanager.authenticate(username, password)

    async def begin_auth(self, username: str) -> bool:
        self.conn_info.username = username

        if self._config.enable_noauth:
            # No authentication.
            return False

        if self._config.enable_keys:
            try:
                keys = await self._config.keymanager.get_keys(username)
                self._conn.set_authorized_keys(keys)
            except ValueError:
                pass

        return True


class SSHCLIServer:
    """
    Implementation of the `asyncssh SSHServer <https://asyncssh.readthedocs.io/en/latest/api.html#sshserver>`_

    :param cli: The CLI to use for incoming ssh connections
    :param config: Server configuration details. Defaults to ServerConfig instance with all defaults.
    """

    def __init__(self,
                 cli: CLI_Handler,
                 config: ServerConfig):
        self.server_task = None
        self.cli = cli

        if config:
            self.config = config
        else:
            self.config = ServerConfig()

        self.is_running: Event = asyncio.Event()

        self._server: Optional[asyncssh.SSHAcceptor] = None

    @property
    def ssh_server(self) -> asyncssh.SSHAcceptor:
        return self._server

    async def close(self):
        self._server.close()
        await self._server.wait_closed()

    async def stop(self):
        await self.close()
        loop = asyncio.get_event_loop()
        loop.stop()

    def _get_host_key(self) -> asyncssh.SSHKey:
        """
        Load the private key for the SSH server.

        If the key file does not exist or is not a valid key a new private key is generated and
        saved.

        :return: the private key for the ssh server
        """
        try:
            key = asyncssh.read_private_key(self.config.server_host_key)
        except (FileNotFoundError, asyncssh.KeyImportError):
            key = asyncssh.generate_private_key('ssh-rsa', 'SSH Server Host Key for ssh_cli_demo')
            try:
                keyfile = open(self.config.server_host_key, 'wb')
                keyfile.write(key.export_private_key())
                keyfile.close()
                print(f"SSH Server: New private host key generated and saved as {self.config.server_host_key}")
            except Exception as exc:
                print(f"SSH Server: could not write host key to {self.config.server_host_key}. Reason: {exc}")

        return key

    def _server_factory(self) -> asyncssh.SSHServer:
        sshserver = _InternalSSHConnection(self.cli, self, self.config)
        self._current_sshserver = sshserver
        return sshserver

    async def start_server_task(self) -> None:
        self.server_task = asyncio.create_task(self.run_server())
        await self.is_running.wait()

    async def run_server(self) -> None:
        """
        Start the SSH server and run until the server is shut down.
        """

        # add something like this to add client authentification:
        # options.authorized_client_keys = ...
        # An alternative would be to subclass PromptToolkitSSHServer and implement the begin_auth() method.

        self._server: asyncssh.SSHAcceptor = await asyncssh.listen("", self.config.port,
                                                                   server_factory=self._server_factory,
                                                                   server_host_keys=self._get_host_key())

        # at this point the server is running. Inform interested listeners.
        logger.info("SSH CLI Server started")
        self.is_running.set()

        await self._server.wait_closed()

        self.is_running.clear()
        print("SSH Server stopped")
