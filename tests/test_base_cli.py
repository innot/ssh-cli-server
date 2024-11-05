#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import asyncio
import logging
import tempfile
import unittest
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from typing import Any, Coroutine

import asyncssh
from argparsedecorator import Choices
from asyncssh import SSHWriter, SSHReader

from ssh_cli_server.base_cli import BaseCLI
from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.ssh_cli_server import SSHCLIServer, CLI_Handler

class TestCLI(BaseCLI):
    cli = BaseCLI.cli
    @cli.command
    async def shutdown(self) -> str:
        return "shutdown"

    @cli.command
    async def exception(self, kind: str) -> None:
        """
        :choices kind: 'SyntaxError', 'TypeError'
        """
        if kind == "SyntaxError":
            raise SyntaxError("test1")
        elif kind == "TypeError":
            raise TypeError("test2")
        else:
            raise ValueError("invalid kind")

class MyTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmpdir.name)
        self.port = 23450

        logging.basicConfig(level=logging.ERROR)


    async def asyncTearDown(self) -> None:
        self.tmpdir.cleanup()

    async def start_test_server(self, cli: CLI_Handler) -> int:
        """
        Start the sshcliserver as an asyncio task and return the port number it is listening to
        :param cli: The AbstractCLI the server is to use.
        :return: Port number
        """
        port = self.get_port()
        config = ServerConfig(self.tmppath, port=port, enable_noath=True)
        self.server = SSHCLIServer(cli, config)
        self.server.start_server_thread()
        return port

    async def stop_test_server(self):
        """
        Stop the currently running sshcliserver task.
        :return:
        """
        if self.server:
            await self.server.close(force=True)
            try:
                await asyncio.wait_for(self.server._server_task, 1.0)
            except TimeoutError:
                self.fail("server did not stop within 1 second.")

    def get_port(self) -> int:
        self.port += 1
        return self.port

    async def test_server_start_and_stop(self):
        # this just tests the base test methods itself
        port = await self.start_test_server(BaseCLI)
        self.assertTrue(self.server._is_running_task.is_set())
        async with self.ssh_connection(port) as stdio:
            stdin, stdout, stderr = stdio
            self.assertIsNotNone(stdin)
            self.assertIsNotNone(stdout)
            self.assertIsNotNone(stderr)

        await self.stop_test_server()
        self.assertFalse(self.server._is_running_task.is_set())

    async def test_exception_logging(self):
        port = await self.start_test_server(BaseCLI)

        async with self.ssh_connection(port) as stdio:
            stdin, stdout, stderr = stdio

            with self.assertLogs(level="INFO") as cm:
                stdin.writelines("exception SyntaxError\n")
                try:
                    await asyncio.wait_for(stdout.readline(), 1000)
                    self.assertTrue("SyntaxError" in cm.output)
                except TimeoutError as exc:
                    raise self.failureException(f"No response from ssh server {exc}") from exc

    async def test_shutdown_command(self):

        port = await self.start_test_server(BaseCLI)

        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:
            self.assertIsNotNone(conn)

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")

            stdin.writelines("shutdown\n")

            try:
                await asyncio.wait_for(stdout.channel.wait_closed(), 1000)
            except TimeoutError as exc:
                raise self.failureException(f"shutdown did not close connection") from exc

        # the server should also have stopped
        self.assertFalse(self.server._is_running_task.is_set())


    @asynccontextmanager
    async def ssh_connection(self, port:int) -> Coroutine[Any, Any, tuple[SSHWriter, SSHReader, SSHReader]]:
        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:
            self.assertIsNotNone(conn)

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")
            try:
                # wait for the connection and eat the prompt
                response: str = await asyncio.wait_for(stdout.readline(), 1)
                self.assertTrue("started" in response)
            except TimeoutError as exc:
                raise self.failureException(f"No response from ssh server {exc}") from exc

            print("connection established")

            yield stdin, stdout, stderr

            print("closing connection")

if __name__ == '__main__':
    unittest.main()
