#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.

from __future__ import annotations

import abc
from abc import abstractmethod

from prompt_toolkit.contrib.ssh import PromptToolkitSSHSession

from ssh_cli_server.connection_info import ConnectionInfo


class AbstractCLI(abc.ABC):
    def __init__(self):
        self._server = None
        self._connection_info = None

    @abstractmethod
    async def interact(self, ssh_session: PromptToolkitSSHSession):
        pass

    @property
    def connection_info(self) -> ConnectionInfo:
        return self._connection_info

    @connection_info.setter
    def connection_info(self, connection_info: ConnectionInfo):
        self._connection_info = connection_info
