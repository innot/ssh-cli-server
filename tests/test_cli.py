#  Copyright (c) 2023 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import asyncio
import sys
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.contrib.ssh import PromptToolkitSSHSession

from ssh_cli_server.abstract_cli import AbstractCLIFactory
from ssh_cli_server.ssh_cli_server import AbstractCLI, SSHCLIPromptToolkitSession


class TestCLI(AbstractCLI):

    def __init__(self):
        super().__init__()
        self.stdout: TextIO = sys.stdout
        self.stdin: TextIO = sys.stdin
        self.prompt_session = None

        # a asyncio task that shuts the server down
        self.shutdown_task = None

    async def interact(self, ssh_session: PromptToolkitSSHSession) -> None:
        """
        Handle an incoming SSH connection.

        This is the entry point to start the CLI in the given SSH session.
        It will display a prompt and execute the user input in a loop until the session is closed, either by the
        remote end or by the 'exit' command.

        This will be called from the ssh server for each incoming connection. There is no need to call it directly.

        :param ssh_session: Session object
        """
        session = ssh_session.app_session
        session.output.write("test server started\n")
        self.prompt_session = PromptSession("TestCLI\r\n")

        loop = True

        while loop:
            try:
                command = await self.prompt_session.prompt_async("#")
                if command == "exit":
                    # close current connection
                    session.output.write("Closing SSH connection\n")
                    session.output.flush()
                    loop = False

                elif command == "shutdown":
                    if self.connection_info.sshserver:
                        session.output.write("SSH Server is shutting down\n")
                        session.output.flush()
                        self.shutdown_task = asyncio.create_task(self.connection_info.sshserver.close())
                        loop = False
                    else:
                        session.output.write(
                            "Could not shut down ssh server: server not set_option\n")
                elif command == "username":
                    session.output.write(self.connection_info.username + "\n")
                    session.output.flush()
                else:
                    # echo the input
                    session.output.write(command + "\n\n")

            except KeyboardInterrupt:
                print("SSH connection closed by Ctrl-C")
                break
            except EOFError:
                # Ctrl-D : ignore
                pass
            finally:
                pass


class TestCLIFactory(AbstractCLIFactory):

    def cli(self):
        return TestCLI()
