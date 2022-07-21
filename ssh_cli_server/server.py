#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.

from __future__ import annotations

import abc
import asyncio
from asyncio import Event
from typing import Callable, Awaitable, Optional

import asyncssh
from prompt_toolkit.contrib.ssh import PromptToolkitSSHSession

from ssh_cli_server.serverconfig import ServerConfig


class AbstractCLI(abc.ABC):
    async def interact(self, ssh_session: PromptToolkitSSHSession):
        pass

    @property
    def sshserver(self) -> asyncssh.SSHAcceptor:
        """
        The SSH Server (actually asyncssh.SSHAcceptor) running this session.
        Must be set_option externally and is used for the :code:`shutdown` command.
        """
        return self._server

    @sshserver.setter
    def sshserver(self, sshserver: asyncssh.SSHAcceptor):
        self._server = sshserver



class ConnectionInfo:
    username: str = ""
    user_agent: str = ""
    server: asyncssh.SSHServer = None


class SSHCLIPromptToolkitSession(PromptToolkitSSHSession):

    def __init__(self,
                 interact: Callable[["PromptToolkitSSHSession"], Awaitable[None]],
                 conn_info: ConnectionInfo) -> None:
        super().__init__(interact)
        self.connection_info = conn_info


class _InternalServerInstance(asyncssh.SSHServer):

    def __init__(
            self,
            interact: Callable[[PromptToolkitSSHSession], Awaitable[None]],
            config: ServerConfig = ServerConfig()
    ) -> None:
        self._interact = interact
        self._config = config
        self.conn_info = ConnectionInfo()
        self.conn_info.server = self

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self._conn = conn
        self.conn_info.user_agent = conn.get_agent_path()

    async def begin_auth(self, username: str) -> bool:
        self.conn_info.username = username

        if self._config.enable_noauth:
            # No authentication.
            return False

        if self._config.enable_keys:
            keys = await self._config.keymanager.get_keys(username)
            self._conn.set_authorized_keys(keys)

        return True

    def session_requested(self) -> PromptToolkitSSHSession:
        return SSHCLIPromptToolkitSession(self._interact, self.conn_info)

    def password_auth_supported(self) -> bool:
        return self._config.enable_passwords

    def validate_password(self, username: str, password: str) -> bool:
        return self._config.passwordmanager.check_pwd(username, password)


class SSHCLIServer:
    """
    Implementation of the `asyncssh SSHServer <https://asyncssh.readthedocs.io/en/latest/api.html#sshserver>`_

    :param cli: The CLI to use for incoming ssh connections
    :param config: Server configuration details. Defaults to ServerConfig instance with all defaults.
    """

    def __init__(self, cli: AbstractCLI, config: ServerConfig = ServerConfig()):
        self.cli = cli
        self.config = config

        self.is_running: Event = asyncio.Event()

        self._server: Optional[asyncssh.SSHAcceptor] = None

    @property
    def ssh_server(self) -> asyncssh.SSHAcceptor:
        return self._server

    async def close(self):
        self._server.close()

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
        sshserver = _InternalServerInstance(self.cli.interact, self.config)
        self._current_sshserver = sshserver
        return sshserver

    async def run_server(self) -> None:
        """
        Start the SSH server and run until the server is shut down.

        :return: Reference to the running server.
        """

        # add something like this to add client authentification:
        # options.author-ized_client_keys = ...
        # An alternative would be to subclass PromptToolkitSSHServer and implement the begin_auth() method.

        self._server: asyncssh.SSHAcceptor = await asyncssh.create_server(
            self._server_factory,
            "",
            self.config.port,
            server_host_keys=self._get_host_key(),
        )
        # TODO: should we propagate this downwards or instead use some kind of event to initiate a server shutdown?
        # self._current_sshserver.parent_server = self._server

        # at this point the server is running. Inform interessted listeners.
        self.is_running.set()
        self.cli.sshserver = self._server

        await self._server.wait_closed()

        self.is_running.clear()
