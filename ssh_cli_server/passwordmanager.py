#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import hashlib
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union, TextIO, Dict

"""
The Passwordmanager checks if a supplied password matches the for a given user.

"""


class AbstractPasswordManager(ABC):
    """
    Base class for any PasswordManager.

    Has only one method:
    :meth:`authenticate` to check that the given password is correct for the given user.

    The actual implementation is up to the subclass and can be anything from a simple Dictionary or File
    (see :class:`SimpleFilePasswordManager`) to an interface to an external user database.
    """
    @abstractmethod
    def authenticate(self, username: str, password: str) -> bool:
        """
        Returns :code:`True` if the given password is valid for the given username.

        :param username: String with the name of the user.
        :param password: String with the password to check against.
        :return: :code:`True` if the password is correct, :code:`False` otherwise.
        """
        pass


class SimpleFilePasswordManager(AbstractPasswordManager):
    """
    Simple password manager implementation that stores username and password in a Dictionary which is
    optionally saved to a simple text file.

    Both the in-memory Dictionary and the file store the password encrypted. By default, the encryption is
    a 128-bit random salt and a 512-bit hash of the password generated with the :external:meth:`~hashlib.scrypt`
    method of the :external:lib:`hashlib` library.
    Override :meth:`encrypt` to implement a different encryption method.

    This simple manager allows any string as a password, including an empty string
    which is equivalent to no password.
    If password quality is an issue check the password before storing it in this manager.

    If the :data:`pwd_file` property is set, all changes to the password "database" are automatically and
    immediatley written to this file. This file will only be loaded either at instantiation (when the 'pwd_file'
    argument is supplied) or with an explicit call to :meth:`load`. External changes to the file are not picked up
    during runtime.
    """

    def __init__(self, pwd_file: Union[str, bytes, Path, TextIO] = None):
        super().__init__()
        self._passwords: Dict[str, str] = {}

        if pwd_file:
            try:
                self._pwd_file: Path = Path(pwd_file)
            except TypeError:      # if the file is a TextIO Stream
                self._pwd_file = pwd_file
            self.load()

        else:
            self._pwd_file = None

    def authenticate(self, username: str, password: str) -> bool:
        if not isinstance(username, str) or not isinstance(password, str):
            # ensure no security hole by passing arbitrary objects
            return False

        if username not in self._passwords:
            return False

        user_pw = self._passwords.get(username)
        salt = bytearray.fromhex(user_pw[:32])
        presented_pw = self.encrypt(password.encode(), salt)

        return user_pw == presented_pw

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
        Add a new username to the password database.

        :param username: Name of the user. Must only contain letters, digits or '_'.
        :param password: The initial password. Can be any string, default is :code:`None` for no password.
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

    def change_password(self, username: str, new_password: str) -> None:
        """
        Change the password for the given user.
        The is first encrypted (hashed with random salt) and stored in the in-memory "database".
        If a :data:`pwd_file` has been set the complete database, including the changed password, is saved.

        :param username: String with existing username.
        :param new_password: The new password.
        :raises ValueError: if the username does not exist in the database.
        """
        if username in self._passwords:
            pw_hex = self.encrypt(new_password.encode())
            self._passwords[username] = pw_hex
            if self.pwd_file:
                self.save()
        else:
            raise ValueError(f"Can't change password for unknown user '{username}.")

    def remove_user(self, username) -> None:
        """
        Remove the user from the database.
        If a :data:`pwd_file` has been set the complete database, without the removed user, is saved.

        :param username: String with the name of the user to be removed.
        :raises: KeyError if the username is unknown.
        """
        self._passwords.pop(username)
        if self.pwd_file:
            self.save()

    def has_user(self, username: str) -> bool:
        """
        Check if the user exists in the database.

        :param username: Name to check.
        :return: :code:`True` if the user exists in the database.
        """
        return username in self._passwords

    @staticmethod
    def encrypt(password: bytes, salt: bytes = None) -> str:
        """
        Encrypt the given password.

        If the 'salt' argument is not supplied, a random 128-bit salt is used to encrypt the password.

        The returned value is a hex string starting with the salt (first 32 hex characters) immediatly followed by
        the 512-bit (128 character) hash of the password.

        :param password: The password as an array of bytes
        :param salt: Optional salt for encrypting the password.
        :return: The salt + password hash as hex string.
        """
        if not salt:
            salt = uuid.uuid4().bytes
        pw_enc = hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1)
        pw_hex = f"{salt.hex()}{pw_enc.hex()}"
        return pw_hex

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

        All lines starting with a '#' are ignored and not preserved, as is any whitespace.

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
