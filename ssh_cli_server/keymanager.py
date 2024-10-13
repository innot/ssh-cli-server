#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import shutil
from abc import ABC
from pathlib import Path
from typing import Union, TextIO, Dict, List, BinaryIO, Optional

import asyncssh
from asyncssh import SSHAuthorizedKeys, SSHKey
from asyncssh.misc import DefTuple

_AuthKeysArg = DefTuple[Union[None, str, List[str], SSHAuthorizedKeys]]
_KeySource = Union[bytes, str, Path, TextIO, BinaryIO, SSHKey]


class AbstractKeyManager(ABC):
    """
    Store and retrieve all keys associated with a single user.

    """

    async def get_keys(self, username: str) -> _AuthKeysArg:
        pass


class SimpleFileKeyManager(AbstractKeyManager):
    """
    Manages public keys of users.
    """

    def __init__(self, key_folder: Union[str, bytes, Path]):
        _key_folder = Path(key_folder)
        self._key_folder = _key_folder

        if not _key_folder.exists():
            _key_folder.mkdir(mode=0o660, parents=True)

        self._key_cache: Dict[str, List[SSHKey]] = {}

        self._load_all()

    async def get_keys(self, username: str) -> SSHAuthorizedKeys:
        self._check_valid_username(username)

        if username not in self._key_cache:
            raise ValueError(f"User {username} has no public keys.")

        keys = self._key_cache[username]
        key_list = SSHAuthorizedKeys()
        for key in keys:
            key_list.load(key.export_public_key().decode(encoding="ascii"))

        return key_list

    def add_user(self, username: str):
        self._check_valid_username(username)
        if username in self._key_cache:
            raise ValueError(f"User '{username}' already exists")
        self._key_cache[username] = []
        user_dir = self._key_folder / Path(username)
        user_dir.mkdir(exist_ok=True)

    @staticmethod
    def read_key(key: _KeySource) -> Optional[SSHKey]:
        """Get the key from the input.
        """
        if isinstance(key, SSHKey):
            return key

        # Check if the key is a file
        try:
            if Path(key).exists():
                return asyncssh.read_public_key(Path(key))
        except TypeError:
            # check if it is an open file
            try:
                content = key.read()
                return asyncssh.import_public_key(content)
            except AttributeError:
                # maybe key is keydata itself
                return asyncssh.import_public_key(key)
        return None

    def add_public_key(self, username: str, key: _KeySource):
        self._check_valid_username(username)

        if username not in self._key_cache:
            self.add_user(username)

        sshkey = self.read_key(key)

        # at this point sshkey should be a valid key
        self._key_cache[username].append(sshkey)

        # save key to user keys directory under its fingerprint
        fingerprint = sshkey.get_fingerprint().split(':')[1]
        key_file = self._key_folder / Path(username) / Path(fingerprint)
        sshkey.export_public_key(str(key_file.resolve()))

    def remove_public_key(self, username: str, key: _KeySource):
        self._check_valid_username(username)

        user_keys = self._key_cache[username]
        stale_key = self.read_key(key)
        stale_fingerprint = stale_key.get_fingerprint()
        for key in user_keys:
            if stale_fingerprint == key.get_fingerprint():
                user_keys.remove(key)
                key_file = self._key_folder / Path(username) / Path(stale_fingerprint.split(':')[0])
                key_file.unlink(missing_ok=True)

    def remove_user(self, username) -> None:
        """
        Remove the user
        :param username:
        :raises: KeyError if the username is unknown.
        """
        self._check_valid_username(username)

        self._key_cache.pop(username)

        # delete all files on disk
        user_folder = self._key_folder / Path(username)
        shutil.rmtree(user_folder)

    def has_user(self, username: str) -> bool:
        self._check_valid_username(username)

        return username in self._key_cache

    def _load_all(self) -> None:
        """
        Load all keys for all users and stores them in the cache.

        """
        for user_folder in self._key_folder.iterdir():
            username = user_folder.name
            keylist = self._key_cache[username]
            for key_file in user_folder.iterdir():
                sshkey = self.read_key(key_file)
                keylist.append(sshkey)

    def generate_user_private_key(self, username: str, comment: str = None) -> SSHKey:
        self._check_valid_username(username)

        if not comment:
            comment = f"SSH CLI Key for user {username}"
        private_key = asyncssh.generate_private_key("ssh-rsa", comment)
        public_key = private_key.export_public_key()
        self.add_public_key(username, public_key)
        return private_key

    @staticmethod
    def _check_valid_username(username: str):
        """Check that the username is a :code:`str` and that it consists only of letters, digits and '_'."""
        if not str(username).isidentifier():
            raise ValueError(f"Username {username} is not a valid username.")
