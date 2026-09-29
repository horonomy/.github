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


class Ownership(enum.Enum):
    """Who owns `statusLine` right now, in ADR-0009's vocabulary.

    The classes that matter are the two that look the same from a distance.
    `HORONOM_OWNED` means both our marker and our command are present, which is
    the only state that authorises us to change the command back. `ADOPTABLE`
    means the command is ours but the marker is not there -- a hand-written
    config, or a marker some editor dropped. We cannot tell those apart, so it
    is treated as unknown ownership and requires the operator to say so
    explicitly rather than being claimed on a guess.
    """

    ABSENT = "absent"
    USER_OWNED = "user_owned"
    HORONOM_OWNED = "horonom_owned"
    ADOPTABLE = "horonom_command_unmarked"
    DRIFTED = "horonom_marker_without_command"
    UNSUPPORTED_SHAPE = "unsupported_shape"


class ChangeKind(enum.Enum):
    """The disclosure categories a plan has to keep apart.

    These are HORO-1000's categories, not ones invented here, because the reason
    they are separate is that they carry different risk: a reader skimming a plan
    needs `product_owned_remove` to look different from `host_user_state_
    preserved` without reading the detail text.
    """

    ADD = "product_owned_add"
    UPDATE = "product_owned_update"
    REMOVE = "product_owned_remove"
    PRESERVED_USER = "host_user_state_preserved"
    PRESERVED_OTHER_PRODUCT = "other_product_state_preserved"
    BLOCKED_UNKNOWN = "unknown_state_blocking_mutation"


# The first indented line in a JSON document, which is enough to recover how the
# rest of it is indented. Anchored on a quote so that indentation inside a
# multi-line string value cannot be mistaken for the document's own.
_INDENT_PATTERN = re.compile(rb'\n([ \t]+)"')


def detect_indent(raw: bytes) -> int | str:
    """The indentation the document already uses, for `json.dump` to reuse.

    Re-serialising with our own preferred formatting would rewrite every line of
    a file we mostly do not own, which turns `git diff` on a dotfiles repo from
    one line into hundreds and makes the honest claim "we changed one key"
    impossible to see. Tabs are returned as a string because that is the form
    `json.dump` wants for them.
    """
    match = _INDENT_PATTERN.search(raw)
    if match is None:
        return DEFAULT_INDENT
    indent = match.group(1)
    return "\t" * len(indent) if indent.startswith(b"\t") else len(indent)


@dataclasses.dataclass(frozen=True)
class SettingsDocument:
    """One read of a settings file: its bytes, its value, and how it was written.

    `raw` is kept alongside `data` because it is the only thing that can answer
    "did this change under us" -- a re-parse cannot, since two different byte
    sequences parse to the same value. `mode` and the formatting fields exist so
    that writing the file back does not quietly restyle or re-permission it.
    """

    path: pathlib.Path
    raw: bytes
    data: dict
    indent: int | str
    trailing_newline: bool
    mode: int | None

    @property
    def existed(self) -> bool:
        """Whether there was a file here at all.

        Distinguished from an empty file because the two differ at uninstall: a
        `statusLine` we created in a file that did not exist is removable down to
        nothing, and one we created in a file the user already had is not.
        """
        return self.mode is not None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def status_line(self) -> object:
        return self.data.get(STATUS_LINE_KEY)


def parse_settings(raw: bytes) -> dict:
    """The settings value, or a refusal.

    An absent or whitespace-only file is an empty configuration, which is an
    ordinary starting state. Anything else that does not yield a JSON object is
    refused: this is
    `MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION`, and the
    behaviour it exists to forbid is the one where a parse error becomes `{}`
    and the next write erases everything.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Reports the position, not the surrounding text: a syntax error near a
        # credential would otherwise quote it back into the terminal.
        raise SettingsParseError(
            f"settings file is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(parsed, dict):
        raise SettingsParseError("settings file must contain a JSON object at the top level")
    return parsed


def read_settings(path: pathlib.Path) -> SettingsDocument:
    """Read and parse the settings file, or refuse.

    The single place a settings file enters this module, so that every operation
    starts from bytes, a fingerprint of those bytes, and the file's existing
    mode -- there is no path by which a later step can read the file again and
    silently disagree with the plan built from this one.
    """
    try:
        raw = path.read_bytes()
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        raw, mode = b"", None
    except OSError as exc:
        # The errno, not the message: on several platforms an EACCES message
        # names every parent directory, and those are the user's paths.
        raise LifecycleError(f"settings file at {path} could not be read (errno {exc.errno})") from exc
    return SettingsDocument(
        path=path,
        raw=raw,
        data=parse_settings(raw),
        indent=detect_indent(raw),
        trailing_newline=raw.endswith(b"\n"),
        mode=mode,
    )


def compositor_path() -> pathlib.Path:
    """Where the compositor this lifecycle installs actually lives."""
    return pathlib.Path(compositor.__file__).resolve()


def compositor_command() -> str:
    """The command string we put in the slot.

    The interpreter is named by absolute path rather than left to `PATH`. The
    host runs this through a shell whose environment we do not control, so
    `python3` may not resolve to the interpreter that has the modules this
    command needs -- and the failure mode of getting that wrong is a statusline
    that works for whoever ran the installer and silently does not for the
    session that matters.
    """
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(compositor_path()))}"


def refers_to_compositor(command: object) -> bool:
    """Whether `command` invokes our compositor.

    Every token is considered, not just the first, because our own command names
    an interpreter first and the compositor second. Comparison is by inode, so a
    relocated checkout or a symlinked path still identifies as ours -- string
    equality against `compositor_command()` would report a stale install as
    somebody else's command and then refuse to release the slot.

    This parses the command, which the compositor is forbidden to do to the
    user's command. The distinction is what the result is used for: asking "is
    this program mine" is inspection, and the string is never reassembled from
    the tokens or handed to a shell in any form but the one it arrived in.
    """
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quoting. Not something this module wrote, and guessing at
        # what the shell would make of it is exactly the parsing we avoid.
        return False
    target = compositor_path()
    for token in tokens:
        candidate = shutil.which(token) or token
        try:
            if os.path.samefile(candidate, target):
                return True
        except OSError:
            continue
    return False


class MarkerState(enum.Enum):
    """Whether the marker slot in a `statusLine` object holds our marker.

    `FOREIGN` is its own answer rather than being folded into `ABSENT`, because
    the two license different actions: we may write a marker where there is
    none, and may not write one over a value we did not put there.
    """

    ABSENT = "absent"
    OURS = "ours"
    FOREIGN = "foreign"


def marker_state(status_line: dict) -> MarkerState:
    found = status_line.get(MARKER_KEY)
    if found is None:
        return MarkerState.ABSENT
    if isinstance(found, dict) and found.get("owner") == MARKER_OWNER:
        return MarkerState.OURS
    return MarkerState.FOREIGN


def classify(document: SettingsDocument) -> Ownership:
    """Who owns the `statusLine` in this document.

    Refuses in every case where the shape is not one this module knows how to
    leave working, which includes an explicit `null` and a `type` we do not
    recognise. Both are cheap to refuse and expensive to guess at: a future
    Claude Code that grows a second `statusLine.type` would have that feature
    silently replaced by ours.
    """
    if STATUS_LINE_KEY not in document.data:
        return Ownership.ABSENT
    status_line = document.status_line
    if not isinstance(status_line, dict):
        return Ownership.UNSUPPORTED_SHAPE
    if status_line.get("type") != SUPPORTED_STATUS_LINE_TYPE:
        return Ownership.UNSUPPORTED_SHAPE
    marker = marker_state(status_line)
    if marker is MarkerState.FOREIGN:
        return Ownership.UNSUPPORTED_SHAPE
    command = status_line.get("command")
    if not isinstance(command, str) or not command.strip():
        return Ownership.UNSUPPORTED_SHAPE
    marked = marker is MarkerState.OURS
    if refers_to_compositor(command):
        return Ownership.HORONOM_OWNED if marked else Ownership.ADOPTABLE
    return Ownership.DRIFTED if marked else Ownership.USER_OWNED


def settings_scope(path: pathlib.Path) -> str:
    """The blast radius of writing this file, in HORO-1000's vocabulary.

    Labelled on every plan rather than stated once in documentation, because the
    difference between "this project" and "every project on this machine" is the
    single most consequential thing about a host-config mutation and the easiest
    to skim past.
    """
    parent = path.expanduser().parent
    if parent == pathlib.Path.home() / ".claude":
        return "user"
    return "project" if parent.name == ".claude" else "other"


@dataclasses.dataclass(frozen=True)
class Change:
    """One line of a plan, in one of HORO-1000's disclosure categories."""

    kind: ChangeKind
    target: str
    detail: str

    def to_json(self) -> dict:
        return {"category": self.kind.value, "target": self.target, "detail": self.detail}


@dataclasses.dataclass(frozen=True)
class Plan:
    """Everything an operation would do, before any of it has been done.

    A plan is built from one read of the settings file and carries that read's
    `fingerprint`, which is what lets `apply` refuse a plan that has gone stale.
    `settings_after` and `registry_after` are the complete documents to be
    written, not patches: the diffing has already happened, so there is no second
    interpretation step between deciding and writing.
    """

    operation: str
    settings_path: pathlib.Path
    ownership: Ownership
    changes: tuple[Change, ...]
    fingerprint: str
    settings_after: dict | None = None
    registry_after: dict | None = None
    refusal: str | None = None
    remediation: tuple[str, ...] = ()

    @property
    def scope(self) -> str:
        return settings_scope(self.settings_path)

    @property
    def mutates(self) -> bool:
        return self.refusal is None and (
            self.settings_after is not None or self.registry_after is not None
        )

    def to_json(self) -> dict:
        return {
            "operation": self.operation,
            "settings_path": str(self.settings_path),
            "scope": self.scope,
            # Never "whole_artifact". The settings file is shared with the host
            # tool and with every other product, so the answer to "what does
            # uninstall do" is always "removes entries", never "removes a file".
            "ownership_model": "shared_artifact",
            "current_owner": self.ownership.value,
            "would_mutate": self.mutates,
            # Stated as its own field, separate from any automation consent, so
            # that a caller passing a --yes equivalent cannot read this as
            # having been waived. Nothing in this module escalates privilege.
            "requires_os_authorization": False,
            "changes": [change.to_json() for change in self.changes],
            "refusal": self.refusal,
            "remediation": list(self.remediation),
        }
