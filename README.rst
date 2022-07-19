SSH CLI Server
##############

.. note::

    This project is in a very early state of development. Set up a release notification if you want to be informed
    about a functional release. You can also add issues if you like this concept and have some suggestions on
    features to implement.

This is a library to provide an SSH Server serving easily customizable command line interfaces.

Its main purpose is to add a command line interface to applications which can then be accessed
remotly via SSH.

It was written to remotly control a large python application running on a headless [#]_ RaspberryPi.
This is probably the most common usecase: remotly control network connected devices without much overhead.

Another usecase is to remotly execute commands on the server in a controlled fashion without granting
access to a full shell (and its associated security risks [#]_).

Example
=======

Here is a very simple program which will run the server (on default port 8822) and implement a single command:

.. code-block::

    import ssh_cli_server as scs
    import asyncio

    class MyCLI(scs.BaseCLI):

        cli = BaseCLI.cli

        @cli.command
        def greet():
            print_html(f"Welcome <b><yellow>{self.username}</yellow></b>. How nice of you to drop by.")

    if __name__ == "__main__":
        server = scs.Server(cli=MyCLI())  # use default config
        asyncio.run(server.run())

Features
========

* Very little overhead required to set up the server and to implement custom commands.

* Native support for `asyncio <https://docs.python.org/3/library/asyncio.html>`_ to run the server
  concurrently without blocking the main application.

* Username:password or key-based authentication schemes can be used as required.

* Uses `argparseDecorator <https://argparsedecorator.readthedocs.io/>`_ to provide
  a rich set of features for implementing commands and input validation.

* Can be integrated into larger applications or run in standalone mode.

* Licensed under the very permissive MIT license.


Dependencies
============

* Python Version 3.8 or above

* `argparseDecorator <https://pypi.org/project/ArgParseDecorator/>`_, used to implement the CLI part

* `asyncssh <https://pypi.org/project/asyncssh/>`_, used to implement the SSH Server part

* `Python Prompt Toolkit <https://pypi.org/project/prompt-toolkit/>`_, used to provide the terminal support
  with colours, code completion and more.

Installation
============

The easiest way to install the SSH CLI Server is to execute

.. code-block::

    pip install ssh_cli_server

This will install the library and, as required, the dependencies

Alternativly the source files can be downloaded from the project
`github page <https://github.com/innot/ssh-cli-server>`_.

Documentation
=============

Comprehensive documentation is available at `readthedocs <https://ssh-cli-server.readthedocs.com/>`_.

Version History
===============

No releases yet.

-----------------------------------------------------


.. [#] running without monitor, keyboard and mouse.
.. [#] caveat: SSH-CLI-Server has not been security tested and may have its own risks.