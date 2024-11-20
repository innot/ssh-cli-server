#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import asyncio
import inspect
import logging
import tempfile
import unittest
from multiprocessing import Process, Event
from pathlib import Path
from signal import SIGINT

import asyncssh
from asyncssh import PermissionDenied, ChannelOpenError, SSHReader
from asyncssh.stream import SSHClientStreamSession, SSHWriter

from ssh_cli_server.abstract_cli import AbstractCLI
from ssh_cli_server.passwordmanager import SimpleFilePasswordManager
from ssh_cli_server.serverconfig import ServerConfig
from ssh_cli_server.ssh_cli_server import SSHCLIServer
from ssh_cli_server.sshcli_prompttoolkit_session import SSHCLIPromptToolkitSession
from tests._test_cli import TestCLI


logging.basicConfig(level=logging.CRITICAL)


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
    port = 23450

    async def start_test_server(self, *args, **kwargs):
        """Run the SSH CLI Server in a seperate Process to ensure that there is no interference
        between the server under test and the tests making connections to the server."""
        self.server_started = Event()
        kwargs['server_started'] = self.server_started
        self.server_process = Process(target=_run_server, args=args, kwargs=kwargs)
        self.server_process.daemon = True
        self.server_process.start()
        try:
            self.server_started.wait(2)
        except asyncio.TimeoutError:
            self.fail("SSH CLI Server did not start within 2 seconds")

    async def stop_server(self):
        """Stop the SSH CLI Server Process."""
        self.server_process.terminate()
        self.server_process.join()

    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmpdir.name)

    async def asyncTearDown(self) -> None:
        self.tmpdir.cleanup()

    @classmethod
    def get_port(cls) -> int:
        """Return an unused port number for each server test to avoid interference in case some tests leave dangling
        Server tasks or threads."""
        cls.port += 1
        return cls.port

    async def test_server(self):
        # print("test: " + inspect.currentframe().f_code.co_name)

        port = self.get_port()

        # try no authentication
        await self.start_test_server(self.tmppath, enable_noauth=True, port=port)

        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:
            self.assertIsNotNone(conn)

            stdin, stdout, stderr = await conn.open_session(term_type="dumb")
            await self.wait_response("started", stdout)

            stdin.writelines("echostring\n")
            await self.wait_response("echostring", stdout)

            stdin.writelines("exit\n")
            try:
                await asyncio.wait_for(stdout.channel.wait_closed(), 1)
            except asyncio.TimeoutError as exc:
                raise self.failureException(f"ssh server did not close connection") from exc

        await self.stop_server()

    async def test_password_auth(self):
        # print("test: " + inspect.currentframe().f_code.co_name)

        port = self.port

        # create a password file manager
        pwdm = SimpleFilePasswordManager()
        pwdm.add_user("foo", "bar")

        await self.start_test_server(self.tmppath, enable_noauth=False, passwordmanager=pwdm, port=port)

        async with asyncssh.connect(host='localhost', port=port, username="foo", password="bar",
                                    known_hosts=None) as conn:
            stdin, stdout, stderr = await conn.open_session(term_type="dumb")
            await self.wait_response("started", stdout)

            stdin.writelines("username\n")
            await self.wait_response("foo", stdout)

        # test bad password
        with self.assertRaises(PermissionDenied):
            async with asyncssh.connect(host='localhost', port=port, username="foo", password="",
                                        known_hosts=None):
                pass

        # test unknown user
        with self.assertRaises(PermissionDenied):
            async with asyncssh.connect(host='localhost', port=port, username="test", password="",
                                        known_hosts=None):
                pass

        await self.stop_server()

    async def test_cli_types(self):
        # print("test: " + inspect.currentframe().f_code.co_name)
        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True)

        # Test with a CLI Class (not instatiated)
        server = SSHCLIServer(TestCLI, serverconfig)
        server.start_server_thread()
        await self.connection_test(port)
        server.close()

        # Test with a CLI Object
        server = SSHCLIServer(TestCLI(), serverconfig)
        server.start_server_thread()
        await self.connection_test(port)
        server.close()

        # Test with an interact method
        server = SSHCLIServer(TestCLI().interact, serverconfig)
        server.start_server_thread()
        await self.connection_test(port)
        server.close()

        # Test with a CLI Factory
        self.flag = False

        def _test_cli() -> AbstractCLI:
            self.flag = True  # to check that this code was executed.
            return TestCLI()

        server = SSHCLIServer(None, serverconfig, cli_factory=_test_cli)
        server.start_server_thread()
        await self.connection_test(port)
        self.assertTrue(self.flag)
        server.close()

    async def connection_test(self, port: int):
        # print("test: " + inspect.currentframe().f_code.co_name)

        conn = await  asyncssh.connect(host='localhost', port=port, known_hosts=None)

        stdin, stdout, stderr = await conn.open_session(term_type="dumb")
        await self.wait_response("started", stdout)

        conn.close()
        await asyncio.sleep(0.1)  # let the connection closure propagate

        pass

    async def test_server_close(self):
        # this also tests multiple restarts of the server
        # print("test: " + inspect.currentframe().f_code.co_name)
        port = self.get_port()
        config = ServerConfig(port=port)
        server = SSHCLIServer(TestCLI(), config)

        # test with a thread
        server.start_server_thread()
        self.assertIsNotNone(server._server_thread)
        self.assertTrue(server.is_running)
        server.close()
        await server.wait_closed()  # has no effect but should not cause any errors
        self.assertFalse(server.is_running)
        self.assertFalse(server._server_thread.is_alive())
        self.assertTrue(server._loop.is_closed())

        # test with a task
        await server.start_server_task()
        self.assertIsNotNone(server._server_task)
        self.assertTrue(server.is_running)
        server.close()
        await server.wait_closed()
        self.assertFalse(server.is_running)
        self.assertTrue(server._server_task.done())
        self.assertFalse(server._loop.is_closed())  # as this is the main event loop it should not have closed

        # test with an open connection
        server.start_server_thread()
        num_connections = 4
        connections = []
        channels = []

        for i in range(num_connections):
            conn = await asyncssh.connect(host='localhost', port=port, known_hosts=None)
            connections.append(conn)
            _, stdout, stderr = await conn.open_session(term_type="dumb")
            await self.wait_response("started", stdout)
            channels.append(stdout.channel)

        await asyncio.sleep(0.1)

        server.close()
        await asyncio.sleep(0.1)  # give the event loop some time to close all connections
        self.assertEqual(len(server.active_connections), 0)
        for chan in channels:
            self.assertTrue(chan.is_closing())
        for conn in connections:
            self.assertTrue(conn.is_closed())
        self.assertFalse(server.is_running)
        self.assertFalse(server._server_thread.is_alive())
        self.assertTrue(server._loop.is_closed())

    async def test_wait_closed(self):
        # print("test: " + inspect.currentframe().f_code.co_name)
        server = SSHCLIServer(TestCLI())
        await server.start_server_task()

        # test that wait_closed() does not return with the server running
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 1)

        # test with close
        server.close()

        try:
            await asyncio.wait_for(server.wait_closed(), 1)
        except asyncio.TimeoutError:
            self.fail("wait_closed() did not return within 1 second after server.close()")
        self.assertFalse(server.is_running)

    async def test_start_as_thread(self):
        # print("test: " + inspect.currentframe().f_code.co_name)
        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True)

        server = SSHCLIServer(TestCLI, serverconfig)
        server.start_server_thread()
        self.assertIsNotNone(server._server_thread)
        self.assertTrue(server.is_running)

        server.close()
        self.assertFalse(server.is_running)

    async def test_start_as_task(self):
        # print("test: " + inspect.currentframe().f_code.co_name)
        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True)

        server = SSHCLIServer(TestCLI, serverconfig)
        await server.start_server_task()
        self.assertIsNotNone(server._server_task)

        server.close()
        await server.wait_closed()
        self.assertFalse(server.is_running)

    async def test_multiple_connections(self):
        # print("test: " + inspect.currentframe().f_code.co_name)

        class _CliCounter(TestCLI):
            counter: int = 0

            async def interact(self, session: SSHCLIPromptToolkitSession):
                self.counter += 1
                await super().interact(session)

        cli = _CliCounter()

        num_connections = 4

        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True, max_connections=num_connections)

        server = SSHCLIServer(cli, serverconfig)
        server.start_server_thread()

        connections = []
        for i in range(num_connections):
            conn = await asyncssh.connect(host='localhost', port=port, known_hosts=None)
            self.assertIsNotNone(conn)
            connections.append(conn)
            _, stdout, stderr = await conn.open_session(term_type="dumb")
            await self.wait_response("started", stdout)

        # The next connection exceeds max_connections and should fail
        async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:
            with self.assertRaises(ChannelOpenError):
                _, stdout, stderr = await conn.open_session(term_type="dumb")

        # Closing the server should also close all connections
        server.close()
        self.assertFalse(server.is_running)
        self.assertTrue(len(server.active_connections) == 0)
        self.assertEqual(num_connections, cli.counter)

        pass

    async def test_ctrl_c(self):
        # print("test: " + inspect.currentframe().f_code.co_name)

        port = self.get_port()
        serverconfig = ServerConfig(self.tmppath, port=port, enable_noauth=True)

        server = SSHCLIServer(TestCLI, serverconfig)
        server.start_server_thread()

        try:
            async with asyncssh.connect(host='localhost', port=port, known_hosts=None) as conn:

                chan, session = await conn.create_session(
                    SSHClientStreamSession, term_type="dumb")  # type: ignore

                session: SSHClientStreamSession
                stdout = SSHReader(session, chan)
                stdin = SSHWriter(session, chan)

                await self.wait_response("started", stdout)

                # Ctrl-C to abort a long-running command
                stdin.write("longtask\n")
                await self.wait_response("longtask started", stdout)
                stdin.write('\x03')  # send ctrl-c
                await self.wait_response("Ctrl-C", stdout)

                # SIGINT to abort a long-running command
                stdin.write("longtask\n")
                await self.wait_response("longtask started", stdout)
                chan.send_signal(SIGINT)
                await self.wait_response("SIGINT", stdout)

                # BREAK Signal to close the connection
                stdin.write("longtask\n")
                await self.wait_response("longtask started", stdout)
                chan.send_break(500)  # this should close the connection
                try:
                    await asyncio.wait_for(chan.wait_closed(), 1)
                except asyncio.TimeoutError:
                    self.fail(f"break did not close connection")
        finally:
            server.close()

    async def wait_response(self, response: str, stdout: SSHReader):
        try:
            while True:
                server_response: str = await asyncio.wait_for(stdout.readline(), 1)
                if response in server_response:
                    break
        except asyncio.TimeoutError:
            self.fail(f"Did not receive '{response}' from ssh server (Timeout)")

    async def test_server_exceptions(self):
        with self.assertRaises(AttributeError):
            # No CLI
            SSHCLIServer(None)

        with self.assertRaises(AttributeError):
            # CLI and cli_factory
            SSHCLIServer(TestCLI(), cli_factory=lambda: TestCLI())

        # Server restart should cause an Exception
        port = self.get_port()
        config = ServerConfig(self.tmppath, port=port, enable_noauth=True)
        server = SSHCLIServer(TestCLI(), config)
        server.start_server_thread()
        with self.assertRaises(RuntimeError):
            await server.start_server_task()
        with self.assertRaises(RuntimeError):
            server.start_server_thread()

        # also run_server can not be called multiple times
        with self.assertRaises(RuntimeError):
            await server.run_server()

        # Using a port twice is also no good. Need to test all start methods as they all have seperate
        # exception handlers
        server2 = SSHCLIServer(TestCLI(), config)

        with self.assertRaises(RuntimeError):
            await server2.run_server()

        with self.assertRaises(RuntimeError):
            server2.start_server_thread()

        with self.assertRaises(RuntimeError):
            await server2.start_server_task()

        server.close()


if __name__ == '__main__':
    unittest.main()
    pass
