#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#

from __future__ import annotations

import asyncio
import logging
import sys
from asyncio import CancelledError
from typing import TextIO, Optional, Dict, Any

from argparsedecorator import ArgParseDecorator
from prompt_toolkit import PromptSession, HTML, print_formatted_text
from prompt_toolkit.completion import Completer, NestedCompleter
from prompt_toolkit.contrib.ssh import PromptToolkitSSHSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from ssh_cli_server.ssh_cli_server import AbstractCLI, SSHCLIPromptToolkitSession

logger = logging.getLogger(__name__)

style = Style.from_dict({
    'error': 'red',
    'warn': 'orange',
    'info': 'grey'
})
"""
Some basic styles. 
Styles can be used as html tags, e.g.:

.. code-block:: html

    <error>An error message</error>

Useful for the :func:`print_html` function.
"""


def print_html(text: str) -> None:
    """
    Format and print text containing HTML tags.

    .. note::
        The prompt toolkit HTML to ANSI converter supports only a few basic HTML tags.
        See `here <https://python-prompt-toolkit.readthedocs.io/en/master/pages/printing_text.html#html>`_
        for more info.

    :param text: A string that may have html tags.
    """
    print_formatted_text(HTML(text), style=style)


def print_error(text: str) -> None:
    """
    Print an error message.

    By default, this message is printed in red. As the text is printed via :func:`print_html` it can include
    HTML tags for further formatting.

    :param text: The message to be printed.
    """
    print_html(f"<error>{text}</error>")


def print_warn(text: str) -> None:
    """
     Print a warning message.

     By default, this message is printed in orange. As the text is printed via :func:`print_html` it can include
     HTML tags for further formatting.

     :param text: The message to be printed.
     """

    print_html(f"<warn>{text}</warn>")


def print_info(text: str) -> None:
    """
     Print an info message.

     By default, this message is printed in grey. As the text is printed via :func:`print_html` it can include
     HTML tags for further formatting.

     :param text: The message to be printed.
     """

    print_html(f"<info>{text}</info>")


class BaseCLI(AbstractCLI):
    """
    Basic Command Line Interface for use with the
    `PromptToolkitSSHServer
    <https://github.com/prompt-toolkit/python-prompt-toolkit/blob/master/src/prompt_toolkit/contrib/ssh/server.py>`_

    It contains all the low-level stuff to integrate it with a PromptToolkit SSH session (in the :meth:`cmdloop` method)

    To add more commands subclass this base CLI, as shown in the :class:`DemoCLI` class.

    .. note::

        Subclasses must use the *ArgParseDecorator* from *BaseCLI*. Do not create a seperate instance.
        The *BaseCLI* instance is in the :data:`BaseCLI.cli` class variable and can be accessed like this:

        .. code-block:: python

            class MyCLI(BaseCLI):
                cli = BaseCLI.cli
                ...

    The other class variables :data:`BaseCLI.intro` and :data:`BaseCLI.prompt` can be overwritten by subclasses.
    """

    intro = "\nThis is a basic SSH CLI.\nType Ctrl-C to exit.\n"
    """Intro text displayed at the start of a new session. Override as required."""

    prompt = "\n<green># </green>"
    """Prompt text to display. Override as required."""

    cli = ArgParseDecorator()
    """The :class:`~.argparse_decorator.ArgParseDecorator` used to decorate command methods."""

    def __init__(self):
        super().__init__()
        self.stdout: TextIO = sys.stdout
        self.stdin: TextIO = sys.stdin
        self.ssh_session: PromptToolkitSSHSession | None = None
        self.prompt_session: PromptSession | None = None
        self.completer: Completer | None = None

        # a asyncio task that shuts the server down
        self.shutdown_task = None

    @property
    def command_dict(self) -> Dict[str, Optional[Dict]]:
        """
        A dictionary with all supported commands suitable for the PromptToolkit
        `Autocompleter <https://python-prompt-toolkit.readthedocs.io/en/master/pages/asking_for_input.html#autocompletion>`_
        """
        # make the command-dict accessible
        return self.cli.command_dict

    #
    # The build in commands
    #

    # noinspection PyMethodMayBeStatic
    def error_handler(self, exc: Exception) -> None:
        """
        Prints any parser error messages in the <error> style (default: red)

        Override this for more elaborate error handling.

        :param exc: Exception containg the error message.
        """
        print_error(str(exc))

    # noinspection PyMethodMayBeStatic
    async def get_prompt_session(self) -> PromptSession:
        """
        Start a new prompt session.

        Called from :meth:`cmdloop` for each new session.

        By default, it will return a simple PromptSession without any argument.
        Override to customize the prompt session.

        :return: a new PromptSession object
        """
        return PromptSession()

    async def run_prompt(self, prompt_session: PromptSession) -> Any:
        """
        Display a prompt to the remote user and wait for his command.

        The default implementation uses only a comand name completer.
        Override to implement other, more advanced features.

        :return: The command entered by the user
        """
        # inititialize command completion
        # List (actually a dict) of all implemented commands is provided by the ArgparseDecorator and
        # used by the prompt_toolkit
        if not self.completer:
            # create the command dict only once
            all_commands = self.command_dict
            self.completer = NestedCompleter.from_nested_dict(all_commands)

        # The prompt visual
        prompt_formatted = HTML(self.prompt)

        return await prompt_session.prompt_async(prompt_formatted, completer=self.completer)

    async def execute(self, cmdline: str) -> Any:
        """
        Excecute the given command.

        :return: 'exit' to close the current ssh session, 'shutdown' to end the ssh server.
                  All other values are ignored.
        """
        result = await self.cli.execute_async(cmdline, base=self, error_handler=self.error_handler, stdout=self.stdout)
        return result

    async def interact(self, ssh_session: SSHCLIPromptToolkitSession) -> None:
        """
        Handle an incoming SSH session.

        This is the entry point to start the CLI in the given SSH session.
        It will display a prompt and execute the user input in a loop until the session is closed, either by the
        remote end or by the 'exit' command.

        This will be called from the ssh server for each incoming connection. There is no need to call it directly.

        :param ssh_session: Session object
        """
        self.ssh_session = ssh_session
        self.prompt_session = PromptSession(refresh_interval=0.5)

        # tell the CLI about stdout (if not using the print_formatted_text() function)
        self.stdout = ssh_session.app_session.output
        self.stdin = ssh_session.app_session.input

        print_formatted_text(HTML(self.intro))

        loop = True
        with patch_stdout():
            while loop:
                try:
                    command = await self.run_prompt(self.prompt_session)
                    if command:
                        result = await self.execute(command)
                        if result == "exit":
                            # close current connection
                            print_warn("Closing SSH connection")
                            loop=False

                        elif result == "shutdown":
                            print_warn("Shutting down server.")
                            self.shutdown_task = asyncio.create_task(self.connection_info.sshserver.close())
                            loop = False

                except CancelledError:
                    # Connection is closed programmatically
                    loop = False
                except KeyboardInterrupt:
                    print_warn("SSH connection closed by Ctrl-C")
                    loop = False
                except EOFError:
                    # Ctrl-D : ignore
                    pass
                except Exception as e:
                    logger.exception(e)

        # exiting loop will close the session & connection
        pass