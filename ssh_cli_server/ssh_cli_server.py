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
from asyncio import AbstractEventLoop, CancelledError
from typing import Callable, Optional, List

import asyncssh
from asyncssh import SSHServerConnection, ChannelOpenError, SSHServerSession

from ssh_cli_server import InteractFunction, CLI_Handler
from ssh_cli_server.abstract_cli import AbstractCLI
from ssh_cli_server.connection_info import ConnectionInfo
from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.sshcli_prompttoolkit_session import SSHCLIPromptToolkitSession

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


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

        self._exception_handler: Callable[[BaseException], None] = self.default_exception_handler

        self._server_lock = asyncio.Lock()
        self._is_running_task = asyncio.Event()
        self._is_running_thread = threading.Event()
        self._is_closed = asyncio.Event()
        self._is_closed.set()
        self._server_condition = threading.Condition()

        self._server_exception: BaseException | None = None

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

    # noinspection PyMethodMayBeStatic
    def default_exception_handler(self, exc: BaseException) -> None:
        """
        The default exception handler just logs the exception.

        :param exc: The Exception to log
        """
        #logger.exception("Exception while running server", exc_info=exc)
        pass

    def set_exception_handler(self, handler: Callable[[BaseException], None] = None) -> None:
        """
        Set a callback for any unhandled exceptions within the server.
        The server can run asynchronously as either an asyncio.Task or in a seperate threading.Thread.
        In both cases exceptions do not bubble out of their Task/Thread Context and are hard to
        retrieve.
        The server exception handler is called with all unhandled exceptions occuring in the server.
        The default exception handler just logs the exception.
        :param handler: A callable which takes an Exception as an argument. `None` to use the default exception handler.
        """
        if handler is not None:
            self._exception_handler = handler
        else:
            self._exception_handler = self.default_exception_handler

    def call_exception_handler(self, exc: BaseException) -> None:
        self._exception_handler(exc)

    def close(self) -> None:
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
        """
        future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)

        # when the server is run as a task then the future can only be fulfilled if the event loop
        # gets a chance to run. But we can't yield to the event loop from a non-async function.
        # Therefore, we need to ignore the future and wait for the closing in wait_closed()

        # if started as a thread wait for the thread to finish as well
        # This ensures that the server is closed when we exit this method
        if self._server_thread:
            self._server_thread.join()

    async def _close(self):
        # close any open connections
        for conn in self.active_connections:
            conn.close()
            await conn.wait_closed()

        self._server.close()
        await self._server.wait_closed()

        # await asyncio.sleep(0)  # hand control to event loop to propagate the closure

        # The run_server method should now continue towards its exit.
        # Let it run in the background and let the close() resp. wait_closed() methods wait for it to finish.

    async def wait_closed(self) -> None:
        """
        Wait for the server to close down.

        This is a coroutine that will block until the server has been closed.
        The only way to cleanly close the server is by calling its :meth:`close` method.

        If the server is already closed (or has not yet started) this method will return immediately.
        """
        await self._is_closed.wait()
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                result = await self._server_task
                if result.exception():
                    self.call_exception_handler(result.exception())
            except asyncio.CancelledError:
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
        if self._is_running_task.is_set() or self._is_running_thread.is_set():
            # only one task/thread can run at once
            raise RuntimeError("SSHCLIServer already started")

        def _thread_runner():
            # start server. This does not return until the server has been stopped.
            logger.debug(f"Server thread started")
            try:
                asyncio.run(self.run_server())  # run server in new event loop. Blocks until server has been closed.
            except BaseException as _exc:
                self._server_exception = _exc
                self.call_exception_handler(_exc)

            # Event loop should be closed once asyncio.run is finished. But sometimes (during debugging) it is not...
            if not self._loop.is_closed():
                self._loop.close()

            with self._server_condition:
                self._server_condition.notify_all()
            logger.debug(f"Server thread finished")

        self._server_thread = threading.Thread(target=_thread_runner, name="SSHCLIServerThread", daemon=True)
        self._server_thread.start()

        # wait for the server to start (or a premature end of thread)
        with self._server_condition:
            self._server_condition.wait(100)

        # server started or raised an exception. Check which:
        if self._is_running_thread.is_set():
            # server is running normally - all is good
            return
        else:
            # server asyncio task has returned prematurly - probably an exception
            if self._server_exception:
                self._server_thread.join()  # wait until server thread is fully terminated
                raise RuntimeError("Server thread raised an exception on startup") from self._server_exception
        # when here then the server is hung (neither started nor an exception)
        raise RuntimeError("Server is hung")

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
        if self._is_running_task.is_set() or self._is_running_thread.is_set():
            # only one task/thread can run at once
            raise RuntimeError("SSHCLIServer already started")

        # start the server...
        self._server_task = asyncio.create_task(self.run_server(), name="server_task")

        # ... and check that it is running and did not throw an Exception
        async def _wait_task():
            await self._is_running_task.wait()

        wait_for_running_task = asyncio.create_task(_wait_task(), name="wait_for_running_task")

        done, pending = await asyncio.wait((self._server_task, wait_for_running_task),
                                           return_when=asyncio.FIRST_COMPLETED)

        if wait_for_running_task in done:
            # server has been started successfully
            await wait_for_running_task
            logger.debug("Server task started")
            return
        else:
            # task finished before running event was set - likely an exception
            wait_for_running_task.cancel()
            try:
                await wait_for_running_task
            except asyncio.CancelledError:
                exception = self._server_task.exception()
                self.call_exception_handler(exception)
                raise RuntimeError("Server task raised an exception on startup") from exception

        def _cleanup(task: asyncio.Task) -> None:
            try:
                _exc = task.exception()
                if _exc is not None:
                    self._server_exception = _exc
                    self.call_exception_handler(_exc)
            except asyncio.CancelledError:
                raise

        self._server_task.add_done_callback(_cleanup)

    async def run_server(self) -> None:
        """
        Start the SSH server and run until the server is shut down.
        """
        if not self._server_lock.locked():
            await self._server_lock.acquire()
        else:
            # server is already started
            raise RuntimeError("run_server() called with server already running")

        try:
            self._loop = asyncio.get_running_loop()
            self._loop.slow_callback_duration = 0.3     # asyncssh is sometimes slow
            self._is_closed.clear()

            self._server: asyncssh.SSHAcceptor = await asyncssh.listen("", self.config.port,
                                                                       server_factory=self._server_factory,
                                                                       server_host_keys=self._get_host_key())
            # at this point the server is running. Inform interested listeners.
            self._is_running_task.set()
            self._is_running_thread.set()
            with self._server_condition:
                self._server_condition.notify_all()
            logger.info(f"SSH CLI Server started on port {self.config.port}")

            # wait for the server to close. This is done by calling self.close(), either internally or externally
            await self._server.wait_closed()
            pass

        except BaseException as exc:
            self._server_exception = exc
            raise RuntimeError(f"Server raised an exception on startup") from exc

        finally:
            self._is_running_task.clear()
            self._is_running_thread.clear()
            # server has been stopped. Inform interested listeners
            self._is_closed.set()
            self._server_lock.release()
            logger.info("SSH CLI Server closed")

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
