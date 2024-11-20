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
from prompt_toolkit.keys import Keys

from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.ssh_cli_server import AbstractCLI, SSHCLIServer
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
                command = await prompt_session.prompt_async("#")
                if command == "exit":
                    # close current connection
                    session.output.write("Closing SSH connection\n")
                    session.output.flush()
                    loop = False
                elif command == "username":
                    session.output.write(ssh_session.username + "\n")
                    session.output.flush()
                elif command == "longtask":
                    session.output.write("longtask started\n")
                    session.output.flush()

                    self._stop_flag = False

                    def ctrl_c_checker():
                        for key_press in session.input.read_keys():
                            print(f"key pressed: {key_press}")
                            if key_press.key == Keys.ControlC:
                                self._stop_flag = True

                    with session.input.raw_mode():
                        with session.input.attach(ctrl_c_checker):

                            for i in range(1, 1000):
                                await asyncio.sleep(0.1)
                                if self._stop_flag:
                                    session.output.write(f"Ctrl-C received (at iteration {i})\n")
                                    session.output.flush()
                                    break
                                if ssh_session.sigint_event.is_set():
                                    ssh_session.sigint_event.clear()
                                    session.output.write(f"SIGINT received (at iteration {i})\n")
                                    session.output.flush()
                                    break


                    session.output.write("longtask finished\n")
                    session.output.flush()

                else:
                    # echo the input
                    session.output.write(command + "\n\n")
            except EOFError:
                session.output.write("EOF @ propmpt received\n")
                session.output.flush()
            except KeyboardInterrupt:
                session.output.write("Ctrl-C @ prompt received\n")
                session.output.flush()


                """
                except CancelledError:
                    # Connection is closed programmatically
                    print("TestCli: CancelledError")
                    raise
                except BaseException as exc:
                    print(f"TestCli.interact exception: {exc}")
                    raise exc
                """
            finally:
                pass
        pass

async def main():
    serverconfig = ServerConfig(port=8889)
    server = SSHCLIServer(TestCLI(), serverconfig)
    await server.start_server_task()
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
