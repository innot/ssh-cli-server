#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.

from __future__ import annotations

import abc
from abc import abstractmethod

from ssh_cli_server.sshcli_prompttoolkit_session import SSHCLIPromptToolkitSession


class AbstractCLI(abc.ABC):
    def __init__(self):
        self._server = None
        self._connection_info = None

    @abstractmethod
    async def interact(self, ssh_session: SSHCLIPromptToolkitSession):
        pass
