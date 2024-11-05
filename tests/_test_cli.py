#  Copyright (c) 2023 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import sys
from asyncio import CancelledError
from typing import TextIO

from prompt_toolkit import PromptSession

from ssh_cli_server.ssh_cli_server import AbstractCLI
from ssh_cli_server.sshcli_prompttoolkit_session import SSHCLIPromptToolkitSession


class TestCLI(AbstractCLI):

    def __init__(self):
        super().__init__()
        self.stdout: TextIO = sys.stdout
        self.stdin: TextIO = sys.stdin
        self.prompt_session = None

        # an asyncio task that shuts the server down
        self.shutdown_task = None

    async def interact(self, ssh_session: SSHCLIPromptToolkitSession) -> None:
        """
        Handle an incoming SSH connection.

        This is the entry point to start the CLI in the given SSH session.
        It will display a prompt and execute the user input in a loop until the session is closed, either by the
        remote end or by the 'exit' command.

        This will be called from the ssh server for each incoming connection. There is no need to call it directly.

        :param ssh_session: Session object

        """
        session = ssh_session.app_session
        prompt_session = PromptSession("TestCLI\r\n")
        session.output.write("test server started\n")

        loop = True
        while loop:
            try:
                print("waiting for prompt")
                command = await prompt_session.prompt_async("#")
                if command == "exit":
                    # close current connection
                    session.output.write("Closing SSH connection\n")
                    session.output.flush()
                    loop = False
                elif command == "username":
                    session.output.write(ssh_session.connection_info.username + "\n")
                    session.output.flush()
                else:
                    # echo the input
                    session.output.write(command + "\n\n")

            except CancelledError:
                # Connection is closed programmatically
                print("TestCli: CancelledError")
                raise
            except BaseException as exc:
                print(f"TestCli.interact exception: {exc}")
                raise exc

        pass