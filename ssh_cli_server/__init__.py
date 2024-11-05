#  Copyright (c) 2022-2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
from typing import TypeAlias, Callable, Coroutine, Any, Union, Type

import ssh_cli_server.abstract_cli
import ssh_cli_server.sshcli_prompttoolkit_session

InteractFunction: TypeAlias = Callable[
    [ssh_cli_server.sshcli_prompttoolkit_session.SSHCLIPromptToolkitSession], Coroutine[Any, Any, None]]
CLI_Handler: TypeAlias = Union[
    Type[ssh_cli_server.abstract_cli.AbstractCLI], ssh_cli_server.abstract_cli.AbstractCLI, InteractFunction]
