#  Copyright (c) 2024 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
from __future__ import annotations

import asyncio
import logging
from asyncio import get_running_loop, CancelledError
from typing import Any, cast, TextIO, Optional

from asyncssh import SSHServerSession
from prompt_toolkit.application import AppSession, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

import ssh_cli_server
from ssh_cli_server.connection_info import ConnectionInfo

logger = logging.getLogger(__name__)


class SSHCLIPromptToolkitSession(SSHServerSession):
    """
    Handler for a single SSH Session.

    """

    def __init__(
            self,
            interact: ssh_cli_server.InteractFunction,
            conn_info: ConnectionInfo) -> None:

        self.interact_function = interact
        self._conn_info = conn_info
        self.enable_cpr = True

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
    def connection_info(self) -> ConnectionInfo:
        return self._conn_info

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

    def session_started(self) -> None:
        self.interact_task = get_running_loop().create_task(self._interact())

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
                    print("Session._interact: CancelledError")
                    raise
                except BaseException as exc:
                    print(f"Session._interact: {exc}")
                    logger.exception("Unhandled Exception", exc_info=True)
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
        if self.interact_task and not self.interact_task.done():
            # self.interact_task.cancel()
            print("Connection_lost: Task cancelled")
        pass
