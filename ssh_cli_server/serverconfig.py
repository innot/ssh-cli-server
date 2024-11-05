# copyright
from pathlib import Path
from typing import Union, Any, Callable, NamedTuple, Mapping

from ssh_cli_server.keymanager import SimpleFileKeyManager, AbstractKeyManager
from ssh_cli_server.passwordmanager import SimpleFilePasswordManager, AbstractPasswordManager


class OptInfo(NamedTuple):
    """
    Named tuple holding information about an option.
    """
    default: Any = None
    converter: Callable[[Any], Any] = lambda x: x  # default: do not convert
    validator: Callable[[Any], bool] = lambda x: True  # default: accept all values


class Options:

    @staticmethod
    def _to_bool(value: Union[str, bool]) -> bool:
        if isinstance(value, bool):
            return value
        if value in ('yes', 'true'):
            return True
        elif value in ('no', 'false'):
            return False
        else:
            raise ValueError(f"{value} is not a True/False value.")

    @staticmethod
    def _to_kwargs(value: Union[Mapping, str]) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if isinstance(value, str):
            # maybe it is a string like "{'key':value}"
            return dict(eval(value))
        raise ValueError(f"{value} can not be used for key/value items")


class ServerConfig(Options):
    """
    :param basedir: Directory where server data is stored. Will be created if it does not exist.
        Default is :code:`.ssh_cli_server/` in the home directory of the user that has started the server.

    :param port: The port number to use for the server. Default is 8822.
    :type port: int between 0 and 65.535
    :param auth_type: Set how to authenticate the remote user.
        * 'none' - do not use any authentication. Only recommended for intranet use behind a firewall.
        * 'password' - with username and password. For this the :code:`passwords` argument is required.
        * 'key' - with the public key of the user.
          Key must be in a file in the :code:`key_dir` with the name of the user.
-        * 'custom' - with a :data:`authenticator` callback function.
        Default is `none`.
    :type auth_type: str
    :param authenticator: Callback function with the
        signature: :code:`authenticate(username:str, password: str, ip_adress: str) -> bool` that will return
        :code:`True` if the user can log on.
    :type authenticator: Callable[[str, str, str], bool]
    """

    # list of all supported keywords (not really required, but makes autocompleter happy)
    port: int
    enable_passwords: bool
    enable_keys: bool
    enable_noauth: bool
    authenticator: Callable[[str, str], bool]
    passwordmanager: AbstractPasswordManager
    keymanager: AbstractKeyManager
    server_host_key: Path
    max_connections: int

    def __init__(self, basedir: Path = None, **kwargs):
        if basedir:
            self.conf_dir = Path(basedir)
        else:
            self.conf_dir: Path = Path.home() / ".ssh_cli_server"

        self.options = {
            # name of the config: OptInfo( default value, converter from string, value validator )
            "port":
                OptInfo(8822, int, lambda x: 0 <= x <= 65535),
            "enable_passwords":
                OptInfo(True, Options._to_bool),
            "enable_keys":
                OptInfo(True, Options._to_bool),
            "enable_noauth":
                OptInfo(True, Options._to_bool),
            "master_key":
                OptInfo(None),
            "auth_type":
                OptInfo("none", str, lambda x: x in ["none", "password", "key", "custom"]),
            "authenticator":
                OptInfo(None, lambda x: x, lambda x: callable(x)),
            "passwordmanager":
                OptInfo(SimpleFilePasswordManager(self.conf_dir / "passwords"),
                        lambda x: x,
                        lambda x: isinstance(x, AbstractPasswordManager)),
            "keymanager":
                OptInfo(SimpleFileKeyManager(self.conf_dir / "user_public_keys"),
                        lambda x: eval(x),
                        lambda x: isinstance(x, AbstractPasswordManager)),
            "server_host_key":
                OptInfo(self.conf_dir / "server_host_key", Path, lambda x: x.exists()),
            "max_connections":
                OptInfo(None, int, lambda x: 0 <= x <= 65535),
        }

        self._asyncssh_kwargs = {}

        # load all defaults
        for option, info in self.options.items():
            default = info.default
            setattr(self, option, default)

        # now collect all known options
        for key in self.options.keys():
            if key in kwargs:
                value = kwargs.pop(key)
                self.set_option(key, value)

        # all remaining arguments are probably for the asyncssh server.
        self._asyncssh_kwargs = kwargs.copy()

    def set_option(self, name: str, value: Any):
        info: OptInfo = self.options[name]
        # convert to required type and validate
        # noinspection PyArgumentList
        real_value = info.converter(value)  # will raise ValueError if value is invalid
        # noinspection PyArgumentList
        if not info.validator(real_value):
            raise ValueError(f"{value} is not a valid value for argument '{name}'")

        # and set_option the value
        setattr(self, name, real_value)
