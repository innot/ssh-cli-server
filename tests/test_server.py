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

from ssh_cli_server.abstract_cli import AbstractCLI
from ssh_cli_server.passwordmanager import SimpleFilePasswordManager
from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.ssh_cli_server import SSHCLIServer
from tests._test_cli import TestCLI


def _run_server(basedir, **kwargs):
    async def _runner():
        server_started = kwargs.pop('server_started')
        serverconfig = ServerConfig(basedir, **kwargs)
        server = SSHCLIServer(TestCLI, serverconfig)
        await server.start_server_task()
        server_started.set()

        await server.wait_closed()  # run until

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

    async def test_run_as_thread(self):
        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True)

        server = SSHCLIServer(TestCLI, serverconfig)
        server.start_server_thread()
        self.assertIsNotNone(server._server_thread)
        self.assertTrue(server.is_running)
        server.close()
        await server.wait_closed()
        self.assertFalse(server.is_running)

    async def test_cli_types(self):
        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True)

        # Test with a CLI Class (not instatiated)
        server = SSHCLIServer(TestCLI, serverconfig)
        await server.start_server_task()
        await self.connection_test(port)
        self.assertTrue(server._is_running.is_set())

        server.close()
        await server.wait_closed()

        # Test with a CLI Object
        server = SSHCLIServer(TestCLI(), serverconfig)
        await server.start_server_task()
        await self.connection_test(port)
        self.assertTrue(server._is_running.is_set())

        server.close()
        await server.wait_closed()

        # Test with a interact method
        server = SSHCLIServer(TestCLI().interact, serverconfig)
        await server.start_server_task()
        await self.connection_test(port)
        self.assertTrue(server._is_running.is_set())

        server.close()
        await server.wait_closed()

        # Test with a CLI Factory
        cli = TestCLI()
        self.flag = False

        def _test_cli() -> AbstractCLI:
            self.flag = True    # to check that this code was executed.
            return cli

        server = SSHCLIServer(None, serverconfig, cli_factory=_test_cli)
        await server.start_server_task()
        await self.connection_test(port)
        self.assertTrue(server._is_running.is_set())
        self.assertTrue(self.flag)

        server.close()
        await server.wait_closed()

    async def connection_test(self, port: int):

        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")

            try:
                response: str = await asyncio.wait_for(stdout.readline(), 1)
                self.assertTrue("started" in response)
            except TimeoutError as exc:
                raise self.failureException(f"No response from ssh server {exc}") from exc

            stdin.writelines("exit\n")
            try:
                await asyncio.wait_for(stdout.channel.wait_closed(), 1)
            except TimeoutError as exc:
                raise self.failureException(f"ssh server did not close connection") from exc

    async def test_server_close(self):
        server = SSHCLIServer(TestCLI())

        # test with a thread
        server.start_server_thread()
        self.assertIsNotNone(server._server_thread)
        self.assertTrue(server.is_running)
        server.close()
        await server.wait_closed()  # has no effect but should not cause any errors
        self.assertFalse(server.is_running)
        self.assertTrue(server._server_thread.done())
        self.assertIsNone(server._loop)

        # test with a task
        await server.start_server_task()
        self.assertIsNotNone(server._server_task)
        self.assertTrue(server.is_running)
        server.close()
        await server.wait_closed()
        self.assertFalse(server.is_running)
        self.assertTrue(server._server_task.done())
        self.assertIsNone(server._loop)

        # test with running run_server directly
        task = asyncio.create_task(server.run_server())
        await server._is_running.wait()
        server.close()
        await server.wait_closed()
        self.assertFalse(server.is_running)
        self.assertTrue(task.done())

    async def test_wait_closed(self):
        server = SSHCLIServer(TestCLI())
        await server.start_server_task()

        # test that wait_closed() does not return with the server running
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(server.wait_closed()), 1)

        # test with close
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), 1)
        except TimeoutError as exc:
            self.fail("wait_closed() did not return within 1 second after server.close()")
        self.assertFalse(server.is_running)

    async def test_start_as_task(self):

        server = SSHCLIServer(TestCLI())
        srvcfg = server.config
        self.assertIsNotNone(srvcfg)
        await server.start_server_task()
        self.assertIsNotNone(server._server_task)

        async with asyncssh.connect(host='localhost', port=srvcfg.port, known_hosts=None) as conn:
            await conn.open_session(term_type="dumb")
            server.close()

        await server.wait_closed()
        self.assertFalse(server.is_running)


if __name__ == '__main__':
    unittest.main()
