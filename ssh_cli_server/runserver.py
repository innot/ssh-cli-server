#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import asyncio

from ssh_cli_server.demo_cli import DemoCLI
from ssh_cli_server.server import SSHCLIServer
from ssh_cli_server.serverconfig import ServerConfig


def main() -> None:
    """
    Start the SSH CLI Server in standalone mode.
    """
    config = ServerConfig(port=8301, enable_noauth=True)
    cli = DemoCLI()
    server = SSHCLIServer(cli, config)
    asyncio.run(server.run_server())

    pass


if __name__ == "__main__":
    main()
