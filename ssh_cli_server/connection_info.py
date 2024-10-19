#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.

from __future__ import annotations

import ipaddress

from asyncssh import SSHServer, SSHServerConnection, SSHServerSession


class ConnectionInfo:
    remote_addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    remote_port: int | None = None
    username: str = ""
    sshserver = None    # the SSHCLIServer object. cannot be type annotated to avoid circular import.
    handler: SSHServer = None
    connection: SSHServerConnection = None
    session: SSHServerSession = None
