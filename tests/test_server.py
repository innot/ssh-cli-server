#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import asyncio
import tempfile
import unittest
from multiprocessing import Process, Event
from pathlib import Path

import asyncssh
from asyncssh import PermissionDenied

from ssh_cli_server.passwordmanager import SimpleFilePasswordManager
from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.ssh_cli_server import SSHCLIServer
from tests.test_cli import TestCLI, TestCLIFactory


def _run_server(basedir, **kwargs):
    async def _runner():
        server_started = kwargs.pop('server_started')
        serverconfig = ServerConfig(basedir, **kwargs)
        server = SSHCLIServer(TestCLI, serverconfig)
        await server.start_server_task()
        server_started.set()

        # wait for the server to finish
        while server.is_running.is_set():
            await asyncio.sleep(0.1)

    asyncio.run(_runner())  # run the server in a new asyncio event loop.


class MyTestCase(unittest.IsolatedAsyncioTestCase):

    async def start_test_server(self, *args, **kwargs):
        self.server_started = Event()
        kwargs['server_started'] = self.server_started
        self.server_process = Process(target=_run_server, args=args, kwargs=kwargs)
        self.server_process.daemon = True
        self.server_process.start()
        try:
            self.server_started.wait(2)
        except TimeoutError:
            self.fail("SSH CLI Server did not start within 2 seconds")

    async def stop_server(self):
        self.server_process.terminate()
        self.server_process.join()

    server: SSHCLIServer = None

    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmpdir.name)
        self.port = 23450

    async def asyncTearDown(self) -> None:
        self.tmpdir.cleanup()

    def get_port(self) -> int:
        self.port += 1
        return self.port

    async def test_server(self):

        # logger = SSHLogger()
        # logger.setLevel(logging.DEBUG)
        # logger.set_debug_level(3)

        port = self.get_port()

        # try no authentication
        await self.start_test_server(self.tmppath, enable_noauth=True, port=port)

        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:
            self.assertIsNotNone(conn)

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")

            try:
                response: str = await asyncio.wait_for(stdout.readline(), 1)
                self.assertTrue("started" in response)
            except TimeoutError as exc:
                raise self.failureException(f"No response from ssh server {exc}") from exc

            stdin.writelines("echostring\n")

            try:
                response = await asyncio.wait_for(stdout.readline(), 1)
                self.assertTrue("echostring" in response)
            except TimeoutError as exc:
                raise self.failureException(f"No response from ssh server {exc}") from exc

            stdin.writelines("exit\n")
            try:
                await asyncio.wait_for(stdout.channel.wait_closed(), 1)
            except TimeoutError as exc:
                raise self.failureException(f"ssh server did not close connection") from exc

        await self.stop_server()

    async def test_password_auth(self):

        port = self.port

        # create a password file manager
        pwdm = SimpleFilePasswordManager()
        pwdm.add_user("foo", "bar")

        await self.start_test_server(self.tmppath, enable_noauth=False, passwordmanager=pwdm, port=port)

        async with asyncssh.connect(host='localhost', port=port, username="foo", password="bar",
                                    known_hosts=None) as conn:

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")

            try:  # disregard all output
                await asyncio.wait_for(stdout.readline(), 1)
            except TimeoutError as exc:
                raise self.failureException(f"No response from ssh server {exc}") from exc

            stdin.writelines("username\n")
            try:
                response: str = await asyncio.wait_for(stdout.readline(), 1)
                self.assertTrue("foo" in response)
            except TimeoutError as exc:
                raise self.failureException(f"No response from ssh server {exc}") from exc

        # test bad password
        with self.assertRaises(PermissionDenied):
            await asyncssh.connect(host='localhost', port=port, username="foo", password="",
                                   known_hosts=None)

        # test unknown user
        with self.assertRaises(PermissionDenied):
            await asyncssh.connect(host='localhost', port=port, username="test", password="",
                                   known_hosts=None)

        await self.stop_server()

    async def test_server_shutdown(self):

        port = self.get_port()
        await self.start_test_server(self.tmppath, enable_noauth=True, port=port)

        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:
            self.assertIsNotNone(conn)

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")

            stdin.writelines("shutdown\n")

            try:
                await asyncio.wait_for(stdout.channel.wait_closed(), 1000)
            except TimeoutError as exc:
                raise self.failureException(f"shutdown did not close connection") from exc

        # the server should also have stopped
        self.server_process.join(1)
        self.assertFalse(self.server_process.is_alive())

    async def test_cli_types(self):
        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath)

        # Test with a CLI Class (not instatiated)
        server = SSHCLIServer(TestCLI, serverconfig)
        await server.start_server_task()
        await asyncio.sleep(1)
        self.assertTrue(server.is_running.is_set())
        await server.close()

        # Test with a CLI Object
        server = SSHCLIServer(TestCLI(), serverconfig)
        await server.start_server_task()
        await asyncio.sleep(1)
        self.assertTrue(server.is_running.is_set())
        await server.close()

        # Test with a interact method
        server = SSHCLIServer(TestCLI().interact, serverconfig)
        await server.start_server_task()
        await asyncio.sleep(1)
        self.assertTrue(server.is_running.is_set())
        await server.close()

        # Test with a CLI Factory
        server = SSHCLIServer(TestCLIFactory(), serverconfig)
        await server.start_server_task()
        await asyncio.sleep(1)
        self.assertTrue(server.is_running.is_set())
        await server.close()


if __name__ == '__main__':
    unittest.main()
