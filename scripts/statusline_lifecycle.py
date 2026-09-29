"""Taking, holding and giving back the one statusline slot Claude Code offers.

The compositor in `statusline_compositor.py` renders a line. Nothing in it
writes to the user's configuration — that is deliberate, and this module is the
other half: the only code in the statusline capability permitted to mutate a
host tool's settings file, and the only place the rules for doing so live.

Those rules come from ADR-0009 and are not negotiable here: everything in the
settings file that is not ours is preserved or the operation aborts. The
practical consequence is that this module never writes a document it did not
first read, never replaces a whole file it does not exclusively own, and never
treats its own past state as authority over what is on disk now. The named
properties each of those corresponds to are in
`governance/product/host-config-ownership-test-contract.md`; the tests in
`test_statusline_lifecycle.py` cite them by ID at the assertion that proves
them, which is the form that document asks for.

Two design choices are worth stating up front because they are the ones a
reader is most likely to assume were oversights.

**Restoration is delta-based, not snapshot-based.** When the slot is released,
the user's original command string is written back into whatever `statusLine`
object is on disk at that moment. We do not keep a copy of their original
object and restore it wholesale, because a user who adjusted `padding` after
enabling us would lose that adjustment — `A + B + C` must become `A + C`, and a
snapshot restore produces `A`. This is why the only thing recorded about the
original is its command string.

**There is no backup of the user's settings file.** Atomicity comes from
writing a temporary file, flushing it to disk and renaming it, so an
interruption leaves either the old file or the new one. A backup would add
nothing to that, and this particular file routinely holds credentials in its
`env` block — copying it somewhere else would be a real exposure bought for a
recovery path we never use. What is recorded instead is a receipt of our own
facts, which contains no user configuration at all.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import enum
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import sys

import statusline_compositor as compositor

# The key Claude Code reads, and the only key in the settings file this module
# is ever allowed to write.
STATUS_LINE_KEY = "statusLine"

# The only `statusLine.type` the compositor can stand in for. A different value
# is a shape we do not understand, and understanding it is a precondition for
# claiming the slot -- see `classify`.
SUPPORTED_STATUS_LINE_TYPE = "command"

# Our ownership marker, written inside the `statusLine` object. Named with a
# leading underscore for the same reason Circinus's `_circinus` hook marker is:
# it signals "not part of the host tool's own schema" to a human reading the
# file, and keeps the two products' markers trivially distinguishable.
MARKER_KEY = "_horonom"
MARKER_OWNER = "horonom-statusline"
MARKER_VERSION = 1

DEFAULT_SETTINGS_PATH = "~/.claude/settings.json"

# Modes for artifacts we create. An existing file's mode is preserved exactly
# rather than normalised to this -- its permissions are host state, not ours.
NEW_FILE_MODE = 0o600
STATE_DIR_MODE = 0o700
STATE_FILE_MODE = 0o600

# Used only when the existing file gives us nothing to copy, because a file we
# create from nothing still has to pick something.
DEFAULT_INDENT = 2

RECEIPT_FILENAME = "receipt.json"


class LifecycleError(Exception):
    """A lifecycle operation refused to proceed.

    Every subclass below means the same thing about the filesystem: nothing was
    written. They are distinguished because the *remediation* differs, and a
    refusal that does not name which conflict blocked it is not much better than
    no refusal at all.
    """


class SettingsParseError(LifecycleError):
    """The settings file is not a JSON object we can read.

    Deliberately not recoverable by writing a fresh default. A file that fails
    to parse is far more likely to be a user's config with a trailing comma than
    an absent one, and the cost of guessing wrong is their whole configuration.
    """


class UnsupportedShapeError(LifecycleError):
    """The settings file parsed, but its `statusLine` is a shape we do not know.

    Separate from `SettingsParseError` because the file itself is fine: it is
    our understanding that is missing, most plausibly because a newer Claude
    Code grew a `statusLine.type` this module predates. Refusing keeps that
    newer feature working; claiming the slot anyway would silently disable it.
    """


class OwnershipError(LifecycleError):
    """We cannot prove we own what we would have to change.

    Covers the unmarked-but-ours case and drift in both directions. This is
    `LEGACY_OWNERSHIP_UNKNOWN_FAILS_SAFE`: an unprovable claim is never
    upgraded to a destructive action just because the alternative is stopping.
    """


class ConcurrentModificationError(LifecycleError):
    """The file changed between being read and being written.

    The plan we were about to apply describes a document that no longer exists,
    and applying it would silently discard whatever the other writer did.
    """


class VerificationError(LifecycleError):
    """The write landed but the read-back does not match the plan.

    Raised rather than swallowed because the alternative is reporting success
    for a state nobody planned. A successful `os.replace` is evidence about the
    filesystem, not about the content.
    """
