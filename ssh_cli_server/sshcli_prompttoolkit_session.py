#  Copyright (c) 2024 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
from __future__ import annotations

import asyncio
import logging
from asyncio import CancelledError, Task
from ipaddress import IPv4Address, IPv6Address
from typing import Any, cast, TextIO, Optional, Union, Tuple, Callable

from asyncssh import SSHServerSession
from prompt_toolkit.application import AppSession, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

import ssh_cli_server
from ssh_cli_server.connection_info import ConnectionInfo

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SSHCLIPromptToolkitSession(SSHServerSession):
    """
    Handler for a single SSH Session.



    """

    # This code is based on the PromptToolkitSSHSession class
    # https://github.com/prompt-toolkit/python-prompt-toolkit/blob/master/src/prompt_toolkit/contrib/ssh/server.py
    # This version improves the error handling, handles connection loses without quitting and adds a number
    # of convenience methods and properties that can be used in the CLI handler.

    def __init__(
            self,
            interact: ssh_cli_server.InteractFunction,
            conn_info: ConnectionInfo) -> None:

        self.interact_function = interact
        self._conn_info = conn_info
        self.enable_cpr = True

        self.sigint_event = asyncio.Event()
        """Event is set if a SIGINT signal has been received from the ssh client."""

        self._sigint_handler: Callable = self._default_sigint_handler
        """Callback function that is called when a SIGINT signal is received.
        The callback should stop a currently running CLI command."""

        self.break_event = asyncio.Event()
        """Event is set if a break signal has been received from the ssh client.
        This can be used by a custom :meth:`break_handler`. The default :meth:`break_handler` just
        tries to close the connection."""

        self._break_handler: Callable[[], bool] = self._default_break_handler
        """Callback function that is called when a break is received from the ssh client.
        The callback should stop the CLI handler and close the connection."""

        self.interact_task: asyncio.Task[None] | None = None
        self.app_session: AppSession | None = None
        self._chan: Any | None = None

        # PipInput object, for sending input in the CLI.
        # (This is something that we can use in the prompt_toolkit event loop,
        # but still write date in manually.)
        self._input: PipeInput | None = None
        self._output: Vt100_Output | None = None

        # Output object. Don't render to the real stdout, but write everything
        # in the SSH channel.
        class Stdout:

            def __init__(self, session: SSHCLIPromptToolkitSession) -> None:
                self.session = session

            def write(self, data: str) -> None:
                try:
                    if self.session._chan is not None:
                        self.session._chan.write(data.replace("\n", "\r\n"))
                except BrokenPipeError:
                    pass  # Channel not open for sending.

            # noinspection PyMethodMayBeStatic
            def isatty(self) -> bool:
                return True

            def flush(self) -> None:
                pass

            @property
            def encoding(self) -> str:
                assert self.session._chan is not None
                # noinspection PyProtectedMember
                return str(self.session._chan._orig_chan.get_encoding()[0])

        self.stdout = cast(TextIO, Stdout(self))

    @property
    def break_handler(self) -> callable:
        """
        A callback function that is called when a break is received from the client.
        The default break handler just sets the :attr:`break_event` Event so that the
        CLI handler can react as required.
        :return: The current break handler
        """
        return self._break_handler

    @break_handler.setter
    def break_handler(self, handler: callable) -> None:
        self._break_handler = handler

    def _default_break_handler(self) -> True:
        # cancel the current interact task to close the connection.
        # Actually the currently running interact_task should be killed, but due to the cooperative nature
        # of asyncio tasks we can just cancel the task and hope that it will actually do that.
        # We could use a thread instead of a task, but that would open a whole new can of worms to make
        # everything threadsafe. Maybe as an option at some later time.
        self.connection_lost(None)
        return True

    @property
    def sigint_handler(self) -> callable:
        """
        A callback function that is called when a SIGINT signal is received from the client.
        The default sigint handler just sets the :attr:`sigint_event` Event so that the
        CLI handler can react as required.
        :return: The current sigint handler
        """
        return self._sigint_handler

    @sigint_handler.setter
    def sigint_handler(self, handler: callable) -> None:
        self._sigint_handler = handler

    def _default_sigint_handler(self) -> None:
        self.sigint_event.set()

    @property
    def username(self) -> str:
        """
        The username given by the client. Can be None if no username is given.
        """
        return self._conn_info.username

    @property
    def client_addr(self) -> Union[IPv4Address, IPv6Address]:
        """
        The IP adress of the client.
        The adress can either be a IPv4 or an IPv6 address.
        """
        return self._conn_info.remote_addr

    @property
    def terminal_size(self) -> Tuple[int, int]:
        """The size of the client terminal as columns and rows."""
        size = self._get_size()
        return size.columns, size.rows

    def _get_size(self) -> Size:
        """
        Callable that returns the current `Size`, required by Vt100_Output.
        """
        if self._chan is None:
            return Size(rows=20, columns=79)
        else:
            width, height, pixwidth, pixheight = self._chan.get_terminal_size()
            return Size(rows=height, columns=width)

    def connection_made(self, chan: Any) -> None:
        self._chan = chan

    def shell_requested(self) -> bool:
        return True

    def _interact_task_done_cb(self, task: Task):
        assert task == self.interact_task

        # check if the task has been cancelled. If yes, then the connection was closed (from either side)
        # No need to do aything
        if task.cancelled():
            return

        # Log if the task finished with an exception
        if task.exception():
            logger.exception("Interact Task raised unhandled exception", exc_info=task.exception())

    def session_started(self) -> None:
        self.interact_task = asyncio.create_task(self._interact(), name="interact_task")
        self.interact_task.add_done_callback(self._interact_task_done_cb)

    async def _interact(self) -> None:
        if self._chan is None:
            # Should not happen.
            raise Exception("`_interact` called before `connection_made`.")

        # noinspection PyProtectedMember
        if hasattr(self._chan, "set_line_mode") and self._chan._editor is not None:
            # Disable the line editing provided by asyncssh. Prompt_toolkit
            # provides the line editing.
            self._chan.set_line_mode(False)

        term = self._chan.get_terminal_type()

        self._output = Vt100_Output(
            self.stdout, self._get_size, term=term, enable_cpr=self.enable_cpr
        )

        with create_pipe_input() as self._input:
            with create_app_session(input=self._input, output=self._output) as session:
                self.app_session = session
                try:
                    await self.interact_function(self)
                except CancelledError:
                    # this is normal if the connection has been closed. No need to log
                    raise
                except BaseException as exc:
                    logger.exception("Unhandled Exception", exc_info=exc)
                    raise
                finally:
                    # Close the connection.
                    self._chan.close()
                    self._input.close()

    def terminal_size_changed(
            self, width: int, height: int, pixwidth: object, pixheight: object
    ) -> None:
        # Send resize event to the current application.
        if self.app_session and self.app_session.app:
            # noinspection PyProtectedMember
            self.app_session.app._on_resize()

    def data_received(self, data: str, datatype: object) -> None:
        if self._input is None:
            # Should not happen.
            return

        self._input.send_text(data)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        # if the server is running in a seperate Thread (i.e. not in the main thread) and
        # is also running in a debugger then the "connection lost" does not reach the
        # PromptToolkit PromptSession that is potentially running.
        # This can cause zombie Threads that prevent the script from existing.

        # Cancel the task manually
        if self.interact_task:
            if not self.interact_task.cancelled():
                self.interact_task.cancel()
                logger.debug("Connection_lost: Task cancelled")
        pass

    def break_received(self, msec: int) -> bool:
        # The client has sent a break signal. Usually that means that the client thinks that the CLI is hanging,
        # and he wants to close the CLI.
        # The standard ssh client implementation can send this with the `~B` escape sequence.
        return self._break_handler()

    def signal_received(self, signal: str) -> None:
        # Caveat: The standard ssh client implementation can not send any signals.
        # Do not put too much effort into this :-)
        if signal == "INT":
            # The client has sent a SIGINT signal. Usually that means that the client wants to interrupt the
            # currently running command.
            self._sigint_handler()

    # I do not know if it makes sense to handle these.
    # def eof_received(self) -> bool:
    #     return False

    # def soft_eof_received(self) -> None:
    #     pass
