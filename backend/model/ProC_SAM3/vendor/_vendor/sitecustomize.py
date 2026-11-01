"""Startup compatibility patches for the local (offline) deployment.

This file is imported automatically by Python because ``_vendor`` is on
``PYTHONPATH`` (see ``run.ps1``).

Problem
-------
On this Windows host the file sandbox grants write access through an inherited
ACE. Directories created with a private mode such as ``0o700`` do **not** carry
that inherited grant, so any later write inside them fails with
``PermissionError``. ``tempfile.mkdtemp`` / ``TemporaryDirectory`` create their
directories with ``0o700``, which breaks ``mmengine.Config.fromfile`` (it copies
a config into a temporary directory) and any other library using temp dirs.

Fix
---
On Windows only, create directories with ``0o777`` instead of ``0o700``.
Windows does not use POSIX permission bits for access control, so this does not
widen access beyond what the inherited sandbox ACE already allows.
"""

import os

if os.name == "nt":
    _original_mkdir = os.mkdir

    def _mkdir(path, mode=0o777, *args, **kwargs):
        # POSIX "owner only" modes (0o700, 0o500, ...) lose the inherited
        # write grant under this sandbox; keep Windows' normal inheritance.
        if isinstance(mode, int) and mode != 0o777 and (mode & 0o077) == 0:
            mode = 0o777
        return _original_mkdir(path, mode, *args, **kwargs)

    os.mkdir = _mkdir
