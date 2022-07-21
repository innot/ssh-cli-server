#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#

from __future__ import annotations

import asyncio
import time

from argparsedecorator import ZeroOrOne
from prompt_toolkit.shortcuts import ProgressBar, yes_no_dialog

from ssh_cli_server.base_cli import BaseCLI, print_info, print_html


class DemoCLI(BaseCLI):
    """
    Demo some features of the `Python prompt toolkit <https://python-prompt-toolkit.readthedocs.io>`_
    """

    intro = "\nThis is a sample CLI to demonstrate the use of the " \
            "<i>argparseDecorator</i> Library with a SSH Server.\n\n" \
            "Use 'help' for all available commands.\n" \
            "Press <b>Ctrl-C</b> to close connection.\n"
    prompt = "\n<green># </green>"

    # get a reference to the BaseCLI ArgParseDecorator
    cli = BaseCLI.cli

    @cli.command
    def exit(self) -> str:
        """exit closes this ssh connection."""
        return "exit"

    @cli.command
    async def shutdown(self) -> str:
        """shutdown the ssh server. All connections will be disconnected."""
        result = await yes_no_dialog("Shutdown", "Are you shure you want to shutdown the server?").run_async()
        if result:
            return "shutdown"
        return ""

    @cli.command
    async def sleep(self, duration: float) -> None:
        """
        Sleep for some time.
        :param duration: sleep time im duration
        """
        print_info("<info>start sleeping</info>")
        t_start = time.time()
        await asyncio.sleep(duration)
        t_end = time.time()
        print_info(f"woke up after {round(t_end - t_start, 3)} seconds")

    @cli.command
    async def progress(self, ticks: int | ZeroOrOne = 50) -> None:
        """
        Show a progress bar.
        :param ticks: Number of ticks in the progressbar. Default is 50
        """
        # Simple progress bar.
        with ProgressBar() as pb:
            for _ in pb(range(ticks)):
                await asyncio.sleep(0.1)

    @cli.command
    async def input(self) -> None:
        """
        Ask for user input.
        Demo for running a new prompt within commands.
        """
        value = await self.promptsession.prompt_async("Enter some random value: ")
        print_html(f"you have entered a value of {value}")

    @cli.command
    async def user(self) -> None:
        """Reports the current user name."""
        username = self.ssh_session.connection_info.username
        print_html(f"You are user '{username}'")
