#  Copyright (c) 2022 Thomas Holland
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see the accompanying LICENSE.txt file or
#  go to <https://opensource.org/licenses/MIT>.
#
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from ssh_cli_server.passwordmanager import SimpleFilePasswordManager


class MyTestCase(unittest.TestCase):

    def test_file_constructor(self):
        with tempfile.TemporaryDirectory() as pwd_dir:
            # generate users and save them
            pwdm = SimpleFilePasswordManager()
            pwdm.add_user("user1", "password")
            pwdm.add_user("user2", "password")
            path = Path(pwd_dir) / "test"
            pwdm.save(path)

            pwdm2 = SimpleFilePasswordManager(path)
            self.assertTrue(pwdm2.has_user("user1"))
            self.assertTrue(pwdm2.has_user("user2"))

    def test_check_pwd(self):
        pwdm = SimpleFilePasswordManager()

        pwdm.add_user("foo", "real_password")

        self.assertTrue(pwdm.check_pwd("foo", "real_password"))
        self.assertFalse(pwdm.check_pwd("foo", "wrong password"))
        self.assertFalse(pwdm.check_pwd("foo", ""))
        self.assertFalse(pwdm.check_pwd("unknown", "real_password"))
        # noinspection PyTypeChecker
        self.assertFalse(pwdm.check_pwd("foo", None))
        # noinspection PyTypeChecker
        self.assertFalse(pwdm.check_pwd(None, None))
        # noinspection PyTypeChecker
        self.assertFalse(pwdm.check_pwd(123, 456))

    def test_user_methods(self):
        pwdm = SimpleFilePasswordManager()

        pwdm.add_user("user")
        self.assertTrue(pwdm.has_user("user"))
        self.assertFalse(pwdm.check_pwd("user", ""))
        pwdm.change_password("user", "password")
        self.assertTrue(pwdm.check_pwd("user", "password"))
        pwdm.remove_user("user")
        self.assertFalse(pwdm.has_user("user"))

    def test_autosaves(self):
        with tempfile.TemporaryDirectory() as pwd_dir:
            pwd_file = Path(pwd_dir) / Path("test")
            pwdm = SimpleFilePasswordManager(pwd_file)
            self.assertFalse(pwd_file.exists())  # should only create when something is saved
            pwdm.add_user("user", "password")
            self.assertTrue(pwd_file.exists())  # should only create when something is saved

            # check if user has been added to file
            with open(pwd_file, "r", encoding="utf-8") as file:
                lines = file.readlines()
                self.assertEqual(1, len(lines))
                self.assertTrue(":" in lines[0])
                name, password = lines[0].split(':')
                self.assertEqual("user", name)

            pwdm.change_password("user", "anotherpassword")
            with open(pwd_file, "r", encoding="utf-8") as file:
                lines = file.readlines()
                self.assertEqual(1, len(lines))
                self.assertTrue(":" in lines[0])
                name, new_password = lines[0].split(':')
                self.assertNotEqual(password, new_password)

            # add a second user and remove the first
            pwdm.add_user("user2")
            with open(pwd_file, "r", encoding="utf-8") as file:
                lines = file.readlines()
                self.assertEqual(2, len(lines))

            pwdm.remove_user("user")
            with open(pwd_file, "r", encoding="utf-8") as file:
                lines = file.readlines()
                self.assertEqual(1, len(lines))
                name, password = lines[0].split(':')
                self.assertEqual("user2", name)

    def test_save_load(self):
        # def if save & load work with opened files
        pwdm = SimpleFilePasswordManager()
        pwdm.add_user("user", "password")
        file = StringIO()
        pwdm.save(file)
        user, password = file.getvalue().split(':')
        self.assertEqual("user", user)
        self.assertGreater(len(password), 32)

        file.seek(0)  # rewind to beginning
        pwdm = SimpleFilePasswordManager()
        pwdm.load(file)
        self.assertTrue(pwdm.check_pwd("user", "password"))


if __name__ == '__main__':
    unittest.main()
