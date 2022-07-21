#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import hashlib
import io
import uuid
from abc import ABC
from pathlib import Path
from typing import Union, TextIO, Dict


class AbstractPasswordManager(ABC):
    """

    """

    def check_pwd(self, username: str, password: str):
        pass


class SimpleFilePasswordManager(AbstractPasswordManager):
    """

    This simple manager allows any string as a password, including an empty string
    which is equivalent to no password.
    If password quality is an issue check the password before storing it in this manager.
    """

    def __init__(self, pwd_file: Union[str, bytes, Path] = None):
        super().__init__()
        self._passwords: Dict[str, str] = {}

        if pwd_file:
            self._pwd_file: Path = Path(pwd_file)
            self.load()
        else:
            self._pwd_file = None

    def check_pwd(self, username: str, password: str) -> bool:
        if not isinstance(username, str) or not isinstance(password, str):
            # ensure no security hole by passing arbitrary objects
            return False

        if username not in self._passwords:
            return False

        user_pw = self._passwords.get(username)
        salt = bytearray.fromhex(user_pw[:32])
        real_pw = bytearray.fromhex(user_pw[32:])
        presented_pw = self.encrypt(password.encode(), salt)

        return real_pw == presented_pw

    @property
    def pwd_file(self) -> Path:
        """
        Path to the passwords file used by this manager.

        Default is :code:`None`
        """
        return self._pwd_file

    @pwd_file.setter
    def pwd_file(self, filename: Union[Path, str, bytes]):
        self._pwd_file = Path(filename)

    def add_user(self, username: str, password: str = None):
        """
        Add a new username to the passwords database.

        :param username: Name of the user. Must only contain letters, digits or '_'.
        :param password:
        :raises ValueError: if the username is already in the database.
        """
        if username in self._passwords:
            raise ValueError(f"User '{username}' already exists.")

        # This is overly restrictive but quick to implement.
        if not username.isidentifier():
            raise ValueError(f"{username} is not a valid username.")

        self._passwords[username] = ""
        if password:
            self.change_password(username, password)
        if self.pwd_file:
            self.save()

    def change_password(self, username: str, new_password: str):
        if username in self._passwords:
            salt = uuid.uuid4()
            pw_enc = self.encrypt(new_password.encode(), salt.bytes)
            pw_hex = f"{salt.hex}{pw_enc.hex()}"
            self._passwords[username] = pw_hex
            if self.pwd_file:
                self.save()
        else:
            raise ValueError(f"Can't change password for unknown user '{username}.")

    def remove_user(self, username) -> None:
        """
        Remove the user
        :param username:
        :raises: KeyError if the username is unknown.
        """
        self._passwords.pop(username)
        if self.pwd_file:
            self.save()

    def has_user(self, username: str) -> bool:
        return username in self._passwords

    @staticmethod
    def encrypt(password: bytes, salt: bytes):
        return hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1)

    def _load(self, pwd_file: TextIO):
        pwd_file.seek(0)  # start at the beginning if this is just some StringIO from unittests
        lines = pwd_file.readlines()
        for line in lines:
            if line.startswith('#'):
                continue  # skip comments
            if ':' in line:
                user, password = line.split(":")
                self._passwords[user.strip()] = password.strip()

    def load(self, pwd_file: Union[Path, str, bytes, TextIO] = None) -> None:
        """
        Load the passwords from a file.

        The passwords file must have one line per user in the format:

        .. code-block::

            username : encrypted_password

        All lines starting with a '#' are ignored, as is any whitespace.

        :param pwd_file: Name of the passwords file. Can be an opened file like a StringIO.
                         Defaults to the filename set with :attr:`pwd_file`
        """
        if not pwd_file:
            pwd_file = self.pwd_file
        try:
            # assume that pwd_file is a filename
            if Path(pwd_file).exists():
                with open(pwd_file, "r", encoding="utf-8") as file:
                    self._load(file)
        except (AttributeError, TypeError):
            # no, lets assume that is a file object. If not an exception is thrown to the caller
            self._load(pwd_file)

    def _save(self, pwd_file: TextIO):
        lines = [username + ":" + password + "\n" for username, password in self._passwords.items()]
        pwd_file.writelines(lines)

    def save(self, pwd_file: Union[Path, str, TextIO] = None) -> None:
        """
        Save the passwords to a file.

        The generated passwords file has one line per user in the format:

        .. code-block::

            username : encrypted_password


        :param pwd_file: Name of the passwords file. Can be an opened file like a StringIO.
                         Defaults to the filename set with :attr:`pwd_file`
        """
        if not pwd_file:
            pwd_file = self.pwd_file

        try:
            with open(pwd_file, "w", encoding="utf-8") as file:
                self._save(file)
                self.pwd_file = Path(pwd_file)  #
        except (AttributeError, TypeError):
            self._save(pwd_file)


if __name__ == "__main__":
    pwds = SimpleFilePasswordManager()
    pwds.add_user("foo", "bar")
    assert pwds.check_pwd("foo", "bar")
    assert pwds.check_pwd("foo", "baz") is False
    f = io.StringIO()
    pwds.save(f)
    print(f.getvalue())
    f.seek(0)

    pwds = SimpleFilePasswordManager()
    pwds.load(f)
    assert pwds.check_pwd("foo", "bar")
    assert pwds.check_pwd("foo", "baz") is False
