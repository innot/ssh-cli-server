#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import ipaddress
import logging
import threading
from asyncio import AbstractEventLoop
from typing import Callable, Optional, List

import asyncssh
from asyncssh import SSHServerConnection, ChannelOpenError, SSHServerSession

from ssh_cli_server import InteractFunction, CLI_Handler
from ssh_cli_server.abstract_cli import AbstractCLI
from ssh_cli_server.connection_info import ConnectionInfo
from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.sshcli_prompttoolkit_session import SSHCLIPromptToolkitSession

logger = logging.getLogger(__name__)


class SSHCLIServer:
    """
    Implementation of the `asyncssh SSHServer <https://asyncssh.readthedocs.io/en/latest/api.html#sshserver>`_

    :param cli: The CLI to use for incoming ssh connections
    :param config: Server configuration details. Defaults to ServerConfig instance with all defaults.
    :param cli_factory: Callable that returns an AbstractCLi instance.
    """

    def __init__(self,
                 cli: CLI_Handler | None,
                 config: ServerConfig | None = None,
                 cli_factory: Callable[[], AbstractCLI] = None):

        self._cli = cli
        self._cli_factory = cli_factory
        if cli is None and cli_factory is None:
            raise AttributeError("You must provide either 'cli' or 'cli_factory' arguments")
        if cli and cli_factory:
            raise AttributeError("Only either 'cli' or 'cli_factory' may be provided, not both.")

        if config:
            self.config = config
        else:
            self.config = ServerConfig()

        self._server_lock: asyncio.Lock = asyncio.Lock()
        self._is_running_task: asyncio.Event = asyncio.Event()
        self._is_running_thread: threading.Event = threading.Event()
        self._is_closed: asyncio.Event = asyncio.Event()
        self._is_closed.set()

        self._server: asyncssh.SSHAcceptor | None = None

        self._connections: List = []

        self._loop: AbstractEventLoop | None = None
        """The Event Loop the server is running in. Either the main loop or, if started as a Thread, 
        a new and seperate loop"""

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._server_thread: threading.Thread | None = None
        """The server background Thread. Set if started as a Thread. Otherwise None."""

        self._server_task: asyncio.Task | None = None
        """The server background task. Set as soon as the server has been started."""

    @property
    def is_running(self) -> bool:
        """
        :return: True if the server is running, False otherwise.
        """
        return self._is_running_thread.is_set()

    @property
    def active_connections(self) -> List[SSHServerConnection]:
        """
        SSHCLIServer maintains a list of all currently active SSH connections
        which can be accessed via this property.

        The returned list is a shallow copy of the internal list.

        :return: List of all currently active connections.
        """
        return self._connections.copy()

    def _add_active_connection(self, connection: asyncssh.SSHServerConnection) -> None:
        self._connections.append(connection)

    def _del_active_connection(self, connection: asyncssh.SSHServerConnection) -> None:
        self._connections.remove(connection)

    def close(self, force: bool = False) -> None:
        """
        Stop the ssh server and exit.
        This method will close all connections gracefully, stop the server and exit the :meth:`run_server` coroutine.
        The Server can be restarted if required.

        If the server has been started via :meth:`start_server_task` (i.e. it is running as a background task)
        then the close is initiated but may not have finished before this method returns to the caller.
        The :meth:`wait_closed()` can be used to wait until the server is truly closed.

        If the server has been started via :meth:`start_server_thread` (i.e. it is running as a background thread
        with its own asyncio.EventLoop) then this method will only return if the thread has exited and the server
        is completely stopped. A call to :meth:`wait_closed` is not required.

        :param force: If True all open connections will be aborted immediatly.
        """
        asyncio.run_coroutine_threadsafe(self._close(force), self._loop)

        # if started as a thread wait for the thread to finish as well
        if self._server_thread:
            self._server_thread.join()  # wait for the thread (if active) to finish

    async def wait_closed(self) -> None:
        """
        Wait for the server to close down.

        This is a coroutine that will block until the server has been closed.
        The only way to cleanly close the server is by calling its :meth:`close` method.

        If the server is already closed (or has not yet started) this method will return immediately.
        """
        await self._is_closed.wait()

    async def _close(self, force: bool = False):
        # close any open connections
        for conn in self.active_connections:
            if force:
                conn.abort()
            else:
                conn.close()

        self._server.close()

        if self._server_task:
            # the server task should stop shortly after the server is closed
            await asyncio.wait_for(self._server_task, timeout=10)
        pass

    def start_server_thread(self) -> None:
        """
        Start the SSHCLIServer in a background thread.

        This thread has its own asyncio event loop, and it will run until the :meth:`close` method of the server
        is called (or the programm terminates).

        This can be used to integrate SSHCLIServer into non-asynchroneous applications.

        .. note::
            The CLI is also run in the generated background thread. Interactions between the CLI and
            the main application should be thread-safe.

        This method returns once the server has been started and is ready to accept connections.

        To cleanly stop the server and the thread call the :meth:`close` method.

        Example::
            server = SSHCLIServer()
            server.start_server_thread()
            # do other stuff
            server.close()  # can be called from somewhere else, e.g. as a rection to a "shutdown" CLI command.

        """

        def _thread_runner():
            # start server. This does not return until the server has been stopped.
            logger.debug(f"Server thread started")
            asyncio.run(self.run_server())  # run server in new event loop. Blocks until server has been closed.
            logger.debug(f"Server thread finished")

        # self._server_thread = self._executor.submit(_thread_runner)
        self._server_thread = threading.Thread(target=_thread_runner, name="SSHCLIServerThread", daemon=True)
        self._server_thread.start()

        # The server should be up and running within 1 second
        if not self._is_running_thread.wait(1):
            self.close(force=True)
            self._server_thread.join()
            raise RuntimeError("SSHCLIServer did not start")

    async def start_server_task(self) -> None:
        """
        Start the SSHCLIServer as an asyncio background task.

        The method returns once the server has been started and is ready to accept connections.

        To cleanly stop the server call the :meth:`close` method followed by
        awaiting :meth:`wait_closed` (if closing of the server must be ensured)

        .. code:: python
            server = SSHCLIServer()
            await server.start_server_task()
            # do other stuff
            server.close()  # can be called from somewhere else, e.g. as a reaction to a "shutdown" CLI command.
            await server.wait_closed()  # block until the server has been closed

        """
        self._server_task = asyncio.create_task(self.run_server(), name="server_task")
        logger.debug("Server task started")
        await self._is_running_task.wait()

        def _cleanup(_: asyncio.Task) -> None:
            logger.debug("Server task finished")

        self._server_task.add_done_callback(_cleanup)

    async def run_server(self) -> None:
        """
        Start the SSH server and run until the server is shut down.
        """
        if self._server_lock.locked():
            # server is already started
            raise RuntimeError("run_server() called with server already running")

        async with self._server_lock:
            self._loop = asyncio.get_running_loop()

            self._is_closed.clear()

            self._server: asyncssh.SSHAcceptor = await asyncssh.listen("", self.config.port,
                                                                       server_factory=self._server_factory,
                                                                       server_host_keys=self._get_host_key())

            # at this point the server is running. Inform interested listeners.
            self._is_running_task.set()
            self._is_running_thread.set()
            logger.info(f"SSH CLI Server started on port {self.config.port}")

            # wait for the server to close. This is done by calling self.close(), either internally or externally
            await self._server.wait_closed()
            self._is_running_task.clear()
            self._is_running_thread.clear()
            self._loop = None

            logger.info("SSH CLI Server closed")

        # server has been stopped. Inform interested listeners
        self._is_closed.set()

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
                logger.info(
                    f"SSH Server: New private host key generated and saved as {self.config.server_host_key}")
            except Exception as exc:
                logger.warning(
                    f"SSH Server: could not write host key to {self.config.server_host_key}. Reason: {exc}")

        return key

    def _server_factory(self) -> asyncssh.SSHServer:
        sshserver = _InternalSSHConnection(self._cli, self._cli_factory, self, self.config)
        return sshserver


class _InternalSSHConnection(asyncssh.SSHServer):
    """This class is created for and handles each single ssh client connection.

        If the connection is successfull a new :class:`SSHCLIPromptToolkitSession` session is
        created and started.

    :param cli:
    """

    def __init__(
            self,
            cli: CLI_Handler,
            cli_factory: Callable[[], AbstractCLI],
            server: SSHCLIServer,
            config: ServerConfig = None,
    ) -> None:

        self._conn = None
        self._cli_handler = cli
        self._cli_factory = cli_factory

        if config:
            self._config = config
        else:
            self._config = ServerConfig()  # use default

        self.conn_info = ConnectionInfo()
        """Stores information about this session."""

        self.conn_info.sshserver = server
        self.conn_info.handler = self

    def connection_made(self, connection: asyncssh.SSHServerConnection) -> None:
        # store information that might be useful
        self.conn_info.connection = connection
        remote = connection.get_extra_info("peername")
        self.conn_info.remote_addr = ipaddress.ip_address(remote[0])
        self.conn_info.remote_port = remote[1]
        self.conn_info.username = connection.get_extra_info("username")

        # noinspection PyProtectedMember
        self.conn_info.sshserver._add_active_connection(connection)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        # noinspection PyProtectedMember
        self.conn_info.sshserver._del_active_connection(self.conn_info.connection)
        if exc:
            logger.warning(f"Client connection closed due to {exc}", exc_info=exc)

    def session_requested(self) -> SSHServerSession:

        # check if this connection would exeed the maximum number of connections.
        if self._config.max_connections:
            # beware: this connection has already been added to the list of activ connections.
            current = len(self.conn_info.sshserver.active_connections)
            if current > self._config.max_connections:
                raise ChannelOpenError(asyncssh.DISC_TOO_MANY_CONNECTIONS, "Maximum number of connections exceeded")

        # create a CLi for this session (if required)
        if self._cli_factory:
            # create a new cli for this session
            cli = self._cli_factory()
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
                async def interact(self, ssh_session: SSHCLIPromptToolkitSession):
                    await interact_method(ssh_session)

            cli = _Cli()

        self.conn_info.session = SSHCLIPromptToolkitSession(cli.interact, self.conn_info)
        return self.conn_info.session

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
