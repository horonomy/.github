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

**There is no backup of the user's settings file, and no separate receipt.**
Atomicity comes from writing a temporary file, flushing it to disk and renaming
it, so an interruption leaves either the old file or the new one. A backup would
add nothing to that, and this particular file routinely holds credentials in its
`env` block — copying it somewhere else would be a real exposure bought for a
recovery path we never use. The record of what we did is the provider registry's
own `lifecycle` block: it holds only our facts, it is the file `apply` re-reads
and re-fingerprints before every write, and the one thing it remembers about the
user — their original command — is only ever consulted when ownership is proven.
A second artifact that nothing reads would be a thing to keep in step, not a
safeguard.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
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
import statusline_contract as contract

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


def detect_indent(raw: bytes) -> int | str | None:
    """The indentation the document already uses, for `json.dump` to reuse.

    Re-serialising with our own preferred formatting would rewrite every line of
    a file we mostly do not own, which turns `git diff` on a dotfiles repo from
    one line into hundreds and makes the honest claim "we changed one key"
    impossible to see. Tabs are returned as a string because that is the form
    `json.dump` wants for them.

    `None` means the document is on one line. It is returned only for a file that
    has content and no indented key, so an empty or missing file still gets
    readable output rather than a minified one.
    """
    match = _INDENT_PATTERN.search(raw)
    if match is not None:
        indent = match.group(1)
        return "\t" * len(indent) if indent.startswith(b"\t") else len(indent)
    return None if raw.strip() else DEFAULT_INDENT


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

    A plan is built from one read of each file it may write and carries both
    reads' fingerprints, which is what lets `apply` refuse a plan that has gone
    stale. Both files are fingerprinted, not just the settings file: the registry
    is where another product's provider entry lives, so a plan formed before a
    concurrent `enable` in another terminal would silently drop that entry.
    `settings_after` and `registry_after` are the complete documents to be
    written, not patches: the diffing has already happened, so there is no second
    interpretation step between deciding and writing.
    """

    operation: str
    settings_path: pathlib.Path
    ownership: Ownership
    changes: tuple[Change, ...]
    fingerprint: str
    registry_path: pathlib.Path | None = None
    registry_fingerprint: str | None = None
    settings_after: dict | None = None
    registry_after: dict | None = None
    state_to_remove: tuple[pathlib.Path, ...] = ()
    notes: tuple[str, ...] = ()
    refusal: str | None = None
    remediation: tuple[str, ...] = ()

    @property
    def scope(self) -> str:
        return settings_scope(self.settings_path)

    @property
    def mutates(self) -> bool:
        return self.refusal is None and (
            self.settings_after is not None
            or self.registry_after is not None
            or bool(self.state_to_remove)
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
            "horonom_state_removed": [str(path) for path in self.state_to_remove],
            "notes": list(self.notes),
            "refusal": self.refusal,
            "remediation": list(self.remediation),
        }


@dataclasses.dataclass(frozen=True)
class ProviderRegistration:
    """What a product tells the host in order to appear in the line.

    The command is an argv sequence, not a shell string, and that asymmetry with
    the upstream command is deliberate. The upstream is a string because it is
    the user's and must be reproduced exactly; a provider's command is supplied
    by a product that knows its own arguments, so there is no reason to involve a
    shell -- and therefore no shell to quote for.
    """

    provider: str
    argv: tuple[str, ...]
    scope: str
    timeout_ms: int | None = None

    def __post_init__(self) -> None:
        contract.require_provider_id(self.provider)
        # Constructed rather than compared so an unrecognised scope raises here,
        # at registration, instead of at the first render.
        contract.Scope(self.scope)
        if not self.argv or not all(isinstance(part, str) and part for part in self.argv):
            raise LifecycleError("a provider command must be a non-empty list of non-empty strings")
        if len(self.argv) > compositor.MAX_ARGV_LENGTH:
            raise LifecycleError(
                f"a provider command may have at most {compositor.MAX_ARGV_LENGTH} parts"
            )
        if self.timeout_ms is not None and not (
            0 < self.timeout_ms <= compositor.MAX_PROVIDER_TIMEOUT_MS
        ):
            raise LifecycleError(
                f"a provider timeout must be between 1 and "
                f"{compositor.MAX_PROVIDER_TIMEOUT_MS} milliseconds"
            )

    def to_entry(self) -> dict:
        entry = {
            "provider": self.provider,
            "command": list(self.argv),
            "scope": self.scope,
            "enabled": True,
        }
        if self.timeout_ms is not None:
            entry["timeout_ms"] = self.timeout_ms
        return entry


@dataclasses.dataclass(frozen=True)
class RegistryDocument:
    """One read of the provider registry.

    `problem` is carried rather than raised so that the diagnostic surface can
    report an unreadable registry instead of dying on it. Every write path checks
    it: a registry we cannot parse may still hold the only record of the user's
    original command, so overwriting it is a decision an operator has to make.
    """

    path: pathlib.Path
    raw: bytes
    present: bool
    data: dict | None
    problem: str | None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def usable(self) -> bool:
        return self.problem is None


def read_registry(home: pathlib.Path | None = None) -> RegistryDocument:
    """Read the registry, validating it with the parser that will actually run it.

    Validation goes through `compositor.parse_registry` rather than a second
    schema kept in step here, so that "the lifecycle wrote it" and "the
    compositor can read it" cannot drift apart into two different ideas of what a
    valid registry is.
    """
    return read_registry_at(compositor.registry_path(home))


def read_registry_at(target: pathlib.Path) -> RegistryDocument:
    """Read the registry at an exact path, for re-reading the one a plan named."""
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return RegistryDocument(path=target, raw=b"", present=False, data=None, problem=None)
    except OSError as exc:
        raise LifecycleError(f"registry at {target} could not be read (errno {exc.errno})") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return RegistryDocument(
            path=target,
            raw=raw,
            present=True,
            data=None,
            problem=f"not valid JSON (line {exc.lineno}, column {exc.colno})",
        )
    try:
        compositor.parse_registry(parsed)
    except compositor.RegistryError as exc:
        return RegistryDocument(path=target, raw=raw, present=True, data=parsed, problem=str(exc))
    return RegistryDocument(path=target, raw=raw, present=True, data=parsed, problem=None)


def empty_registry() -> dict:
    """The document a first install starts from.

    Carries no timestamp, and neither does anything else written into the
    registry. That is what makes repeated enabling idempotent structurally rather
    than by everyone remembering to compare before writing: the registry is a
    function of the intended state and nothing else, so an operation that changes
    nothing produces a byte-identical document. Times are recorded in the
    receipt, which is only written when something actually changed.
    """
    return {"registry_version": compositor.REGISTRY_VERSION, "providers": []}


def _upstream_of(registry: RegistryDocument) -> str | None:
    upstream = (registry.data or {}).get("upstream")
    return upstream.get("command") if isinstance(upstream, dict) else None


def _lifecycle_of(registry: RegistryDocument) -> dict:
    block = (registry.data or {}).get("lifecycle")
    return block if isinstance(block, dict) else {}


def _provider_ids(registry: RegistryDocument) -> tuple[str, ...]:
    entries = (registry.data or {}).get("providers", [])
    if not isinstance(entries, list):
        return ()
    return tuple(
        entry["provider"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("provider"), str)
    )


def _registry_with(
    registry: RegistryDocument,
    registration: ProviderRegistration,
    *,
    upstream: str | None,
    settings_path: pathlib.Path,
    created: object,
) -> dict:
    """The registry document after this provider is registered.

    An existing entry for the same provider is replaced in place rather than
    removed and appended, so re-enabling a provider that is already registered
    does not reorder the document. Ordering does not affect what gets rendered --
    the contract owns that -- but a write that reorders is still a write, and
    this one has no reason to be.
    """
    base = dict(registry.data) if (registry.usable and registry.data) else empty_registry()
    base["registry_version"] = compositor.REGISTRY_VERSION
    entries = [dict(entry) if isinstance(entry, dict) else entry for entry in base.get("providers", [])]
    replacement = registration.to_entry()
    for index, existing in enumerate(entries):
        if isinstance(existing, dict) and existing.get("provider") == registration.provider:
            entries[index] = replacement
            break
    else:
        entries.append(replacement)
    base["providers"] = entries
    if upstream is None:
        base.pop("upstream", None)
    else:
        base["upstream"] = {"command": upstream}
    base["lifecycle"] = {
        "settings_path": str(settings_path),
        "created_status_line": created,
        "installed_command": compositor_command(),
        "marker_version": MARKER_VERSION,
    }
    return base


def _enable_refusal(
    ownership: Ownership, registry: RegistryDocument, adopt: bool
) -> tuple[str | None, tuple[str, ...]]:
    """Why enabling must not proceed, or `(None, ())` if it may.

    Every refusal here exists because proceeding would lose something we cannot
    get back: either the user's own statusline, or the only record of what their
    statusline used to be. `--adopt` is the user saying they accept that loss;
    it is deliberately one flag with one meaning, because a user who has to
    choose between three flags to get past a refusal has not understood any of
    them.
    """
    if ownership is Ownership.UNSUPPORTED_SHAPE:
        return (
            "the configured statusLine is a shape this version does not understand, "
            "so routing it through the compositor could silently disable it",
            (
                f"inspect {STATUS_LINE_KEY} in the settings file by hand",
                "upgrade Horonom if this shape is newer than this version",
            ),
        )
    if ownership is Ownership.DRIFTED:
        return (
            "CONFIG_DRIFT: the statusLine carries a Horonom ownership marker but its "
            "command is not the compositor, so it was changed outside this lifecycle",
            (
                "run `doctor` to see the observed state",
                "re-run with --adopt to take the slot and abandon the recorded original",
            ),
        )
    if ownership is Ownership.ADOPTABLE and not adopt:
        return (
            "the configured command is the Horonom compositor but carries no ownership "
            "marker, so this install cannot prove the configuration is its own",
            ("re-run with --adopt if this configuration is yours",),
        )
    if ownership is Ownership.HORONOM_OWNED and not registry.present and not adopt:
        return (
            "the compositor owns the statusline but its registry is missing, and the "
            "registry holds the only record of the original statusline command",
            (
                "restore the registry from wherever it went",
                "re-run with --adopt to continue without a recorded original",
            ),
        )
    if registry.present and not registry.usable and not adopt:
        return (
            f"the provider registry is unusable ({registry.problem}), and overwriting it "
            "would discard both the other registered providers and the recorded original",
            (
                f"repair or delete {registry.path.name} by hand",
                "re-run with --adopt to start a fresh registry",
            ),
        )
    return (None, ())


def _preservation_changes(
    document: SettingsDocument, others: tuple[str, ...], *, include_status_line: bool = True
) -> list[Change]:
    """Disclosure of what this operation is deliberately leaving alone.

    Key *names* are listed; values never are. This file routinely holds
    credentials in its `env` block, and a plan output is exactly the kind of
    thing a user pastes into a bug report.
    """
    changes: list[Change] = []
    unrelated = [key for key in document.data if key != STATUS_LINE_KEY]
    if unrelated:
        changes.append(
            Change(
                ChangeKind.PRESERVED_USER,
                "settings",
                f"{len(unrelated)} unrelated top-level key(s) unchanged: {', '.join(unrelated)}",
            )
        )
    status_line = document.status_line
    if include_status_line and isinstance(status_line, dict):
        kept = [key for key in status_line if key not in ("command", MARKER_KEY)]
        if kept:
            changes.append(
                Change(
                    ChangeKind.PRESERVED_USER,
                    STATUS_LINE_KEY,
                    f"{len(kept)} existing statusLine key(s) unchanged: {', '.join(kept)}",
                )
            )
    if others:
        changes.append(
            Change(
                ChangeKind.PRESERVED_OTHER_PRODUCT,
                "registry.providers",
                f"{len(others)} other provider(s) left registered: {', '.join(others)}",
            )
        )
    return changes


def plan_enable(
    document: SettingsDocument,
    registry: RegistryDocument,
    registration: ProviderRegistration,
    *,
    adopt: bool = False,
) -> Plan:
    """What enabling this provider would change, without changing anything.

    The returned plan is the only thing `apply` will act on, and it carries the
    fingerprint of the settings file it was formed against so that applying a
    plan to state that has since moved on is a refusal rather than a clobber.
    """
    ownership = classify(document)
    refusal, remediation = _enable_refusal(ownership, registry, adopt)
    if refusal is not None:
        return Plan(
            operation="enable",
            settings_path=document.path,
            ownership=ownership,
            changes=(Change(ChangeKind.BLOCKED_UNKNOWN, STATUS_LINE_KEY, refusal),),
            fingerprint=document.fingerprint,
            registry_path=registry.path,
            registry_fingerprint=registry.fingerprint,
            refusal=refusal,
            remediation=remediation,
        )

    before = document.status_line if isinstance(document.status_line, dict) else None
    changes: list[Change] = []
    if before is None:
        status_line = {"type": SUPPORTED_STATUS_LINE_TYPE}
        upstream: str | None = None
        created: object = True
        changes.append(
            Change(ChangeKind.ADD, STATUS_LINE_KEY, "created; no statusline was configured")
        )
    else:
        status_line = dict(before)
        # Only a proven prior install may hand down a recorded original. An
        # adopted configuration's registry might name a command that stopped
        # being the user's statusline long ago, and restoring that later would
        # write a stale command into their config -- which is the very thing
        # this lifecycle exists to prevent.
        proven = ownership is Ownership.HORONOM_OWNED and registry.usable
        if ownership is Ownership.USER_OWNED:
            upstream = before["command"]
            created = False
            changes.append(
                Change(
                    ChangeKind.UPDATE,
                    f"{STATUS_LINE_KEY}.command",
                    "routed through the compositor; the existing command is registered as the "
                    "upstream provider, run first, and its output kept at the front of the line",
                )
            )
        else:
            upstream = _upstream_of(registry) if proven else None
            created = _lifecycle_of(registry).get("created_status_line") if proven else None

    status_line["command"] = compositor_command()
    status_line[MARKER_KEY] = {"owner": MARKER_OWNER, "version": MARKER_VERSION}
    settings_after = dict(document.data)
    settings_after[STATUS_LINE_KEY] = status_line

    registry_after = _registry_with(
        registry,
        registration,
        upstream=upstream,
        settings_path=document.path,
        created=created,
    )
    already = registration.provider in _provider_ids(registry)
    changes.append(
        Change(
            ChangeKind.UPDATE if already else ChangeKind.ADD,
            f"registry.providers[{registration.provider}]",
            f"{registration.scope}-scoped provider running {shlex.join(registration.argv)}",
        )
    )
    others = tuple(
        provider for provider in _provider_ids(registry) if provider != registration.provider
    )
    changes.extend(_preservation_changes(document, others))

    return Plan(
        operation="enable",
        settings_path=document.path,
        ownership=ownership,
        changes=tuple(changes),
        fingerprint=document.fingerprint,
        registry_path=registry.path,
        registry_fingerprint=registry.fingerprint,
        settings_after=None if settings_after == document.data else settings_after,
        registry_after=None if registry_after == registry.data else registry_after,
    )


def serialize(
    data: dict, *, indent: int | str | None = DEFAULT_INDENT, trailing_newline: bool = True
) -> bytes:
    """Render a settings document back to bytes as close to how it arrived as JSON allows.

    `ensure_ascii=False` matters more than it looks: escaping non-ASCII would
    rewrite every line of a settings file containing an emoji or a non-Latin path
    into `\\uXXXX` form. The file would still parse, and the user's next `git
    diff` of their dotfiles would be unreadable. The indent style and trailing
    newline come from the file we read for the same reason -- a mutation that
    reformats the parts it does not own is still a mutation of them.
    """
    if indent is None:
        # A one-line document stays one line. The compact separators match what a
        # generator like JSON.stringify emits, so the common machine-written file
        # round-trips byte for byte instead of being quietly reflowed by us.
        text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(data, indent=indent, ensure_ascii=False)
    return (text + "\n" if trailing_newline else text).encode("utf-8")


def atomic_write(path: pathlib.Path, payload: bytes, *, mode: int) -> None:
    """Replace a file's contents such that an interruption leaves it intact.

    Write to a sibling temporary file, flush it all the way to the platter, then
    rename over the target: a reader at any instant sees either the old file or
    the new one, never a half-written one. The `fsync` before the rename is the
    part that is easy to leave out and impossible to notice missing, because
    without it the rename can reach disk before the data it renames and a crash
    leaves a correctly-named empty file.

    The directory itself is synced afterwards so the rename survives the same
    crash it was there to protect against.

    `mode` is applied explicitly rather than left to the `open` call because the
    umask would silently narrow or widen it, and this function is used to
    preserve the permissions a file already had.
    """
    try:
        path.parent.mkdir(mode=STATE_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise LifecycleError(
            f"the directory for {path} could not be prepared (errno {exc.errno}); "
            "nothing was changed"
        ) from exc
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise LifecycleError(f"writing {path} failed (errno {exc.errno}); nothing was changed") from exc
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclasses.dataclass(frozen=True)
class ApplyResult:
    """What an applied plan actually did, including that the result was read back.

    `verified` exists as a reported fact rather than an internal assumption
    because "the write returned successfully" and "the file on disk now says what
    we intended" are different claims, and only the second one is worth telling a
    user (HORO-1000's after-mutation question 7).
    """

    plan: Plan
    settings_written: bool
    registry_written: bool
    verified: bool

    def to_json(self) -> dict:
        return {
            "operation": self.plan.operation,
            "settings_written": self.settings_written,
            "registry_written": self.registry_written,
            "read_back_verified": self.verified,
            "changes": [change.to_json() for change in self.plan.changes],
        }


def _releases_slot(plan: Plan) -> bool:
    """Whether applying this plan hands the statusline back to the user."""
    if plan.settings_after is None:
        return False
    status_line = plan.settings_after.get(STATUS_LINE_KEY)
    command = status_line.get("command") if isinstance(status_line, dict) else None
    return not refers_to_compositor(command)


def _verify(plan: Plan) -> None:
    """Read both files back and refuse to call the operation a success unless they match.

    The registry is additionally re-validated through the compositor's own
    parser, because a registry we can read and the compositor cannot is a
    statusline that renders nothing -- the interesting failure is not "did our
    bytes land" but "is the tool still working".
    """
    if plan.settings_after is not None:
        landed = read_settings(plan.settings_path).data
        if landed != plan.settings_after:
            raise VerificationError(
                f"{plan.settings_path} does not match the plan after writing it; "
                "the file has been left as written and needs inspection"
            )
    if plan.registry_after is not None and plan.registry_path is not None:
        after = read_registry_at(plan.registry_path)
        if after.data != plan.registry_after:
            raise VerificationError(f"{plan.registry_path} does not match the plan after writing it")
        if not after.usable:
            raise VerificationError(
                f"the registry just written is not one the compositor will accept ({after.problem})"
            )
    for path in plan.state_to_remove:
        if path.exists():
            raise VerificationError(f"{path} still exists after it was removed")


def apply(plan: Plan) -> ApplyResult:
    """Carry out a plan, or refuse to.

    Both files are re-read and re-fingerprinted first: a plan is a statement
    about state that was true when it was formed, and applying it to state that
    has since moved is exactly the clobber this lifecycle exists to prevent.

    The two writes are ordered so that an interruption between them can only
    leave the user's own statusline working. Taking the slot writes the registry
    first, so a crash leaves the original command recorded but still in charge.
    Giving the slot back writes the settings first, so a crash leaves the
    original command restored and merely a stale registry behind it. In both
    directions the file that could strand the user is written last.
    """
    if plan.refusal is not None:
        raise OwnershipError(plan.refusal)
    if not plan.mutates:
        return ApplyResult(plan=plan, settings_written=False, registry_written=False, verified=True)

    current = read_settings(plan.settings_path)
    if current.fingerprint != plan.fingerprint:
        raise ConcurrentModificationError(
            f"{plan.settings_path} changed after this plan was formed; nothing was written"
        )
    if plan.registry_path is not None and plan.registry_fingerprint is not None:
        if read_registry_at(plan.registry_path).fingerprint != plan.registry_fingerprint:
            raise ConcurrentModificationError(
                f"{plan.registry_path} changed after this plan was formed; nothing was written"
            )

    settings_write = None
    if plan.settings_after is not None:
        settings_write = (
            plan.settings_path,
            serialize(
                plan.settings_after,
                indent=current.indent,
                trailing_newline=current.trailing_newline,
            ),
            current.mode if current.mode is not None else NEW_FILE_MODE,
        )
    registry_write = None
    if plan.registry_after is not None and plan.registry_path is not None:
        registry_write = (plan.registry_path, serialize(plan.registry_after), STATE_FILE_MODE)

    order = (settings_write, registry_write) if _releases_slot(plan) else (registry_write, settings_write)
    for target, payload, mode in [write for write in order if write is not None]:
        atomic_write(target, payload, mode=mode)

    # Deletions come last and only ever name state this module created. The
    # settings file is never in this list: it is shared with the host tool and
    # every other product, so removal takes entries out of it and never takes it
    # away (SHARED_CONFIG_IS_NEVER_DELETED_BY_DEFAULT).
    for path in plan.state_to_remove:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    _verify(plan)
    return ApplyResult(
        plan=plan,
        settings_written=settings_write is not None,
        registry_written=registry_write is not None,
        verified=True,
    )


def _remove_refusal(
    ownership: Ownership, registry: RegistryDocument
) -> tuple[str | None, tuple[str, ...]]:
    """Why a removal must not proceed, or `(None, ())` if it may.

    Note what is *not* here: neither `USER_OWNED` nor `DRIFTED` is a refusal.
    Both mean the slot is no longer ours, and a user who has taken their
    statusline back must still be able to unregister providers -- refusing would
    leave them with state they cannot remove. What those states do prevent is
    writing a command into their configuration, which is handled where the
    restoration is planned rather than by blocking the whole operation.
    """
    if ownership is Ownership.UNSUPPORTED_SHAPE:
        return (
            "the configured statusLine is a shape this version does not understand, so it "
            "cannot be told apart from one this lifecycle installed",
            (f"inspect {STATUS_LINE_KEY} in the settings file by hand",),
        )
    if registry.present and not registry.usable:
        return (
            f"the provider registry is unusable ({registry.problem}), so removing one entry "
            "would mean rewriting it from scratch and losing whatever else it holds",
            (f"repair or delete {registry.path.name} by hand",),
        )
    return (None, ())


def _released_status_line(
    before: dict, ownership: Ownership, registry: RegistryDocument
) -> tuple[dict | None, list[Change], list[str]]:
    """The statusLine object after the slot is given back, and what that cost.

    Restoration is a delta: the recorded command is written into the object that
    is on disk now, so a `padding` the user changed after installing survives.
    The recorded command is never authority over anything else in the object, and
    never authority at all unless ownership is proven -- which is what stops a
    remembered command from overwriting a newer one the user chose themselves.
    """
    after = dict(before)
    after.pop(MARKER_KEY, None)
    changes = [Change(ChangeKind.REMOVE, f"{STATUS_LINE_KEY}.{MARKER_KEY}", "ownership marker removed")]
    notes: list[str] = []

    if ownership is not Ownership.HORONOM_OWNED:
        after["command"] = before.get("command")
        changes.append(
            Change(
                ChangeKind.BLOCKED_UNKNOWN,
                f"{STATUS_LINE_KEY}.command",
                "CONFIG_DRIFT: the configured command is not the compositor this operation is "
                "removing, so it is left exactly as it is",
            )
        )
        notes.append(
            "the statusline was changed outside this lifecycle; no command was written back"
        )
        return after, changes, notes

    recorded = _upstream_of(registry)
    if recorded is not None:
        after["command"] = recorded
        changes.append(
            Change(
                ChangeKind.UPDATE,
                f"{STATUS_LINE_KEY}.command",
                "the command registered as upstream is put back in charge of the statusline",
            )
        )
        return after, changes, notes

    after.pop("command", None)
    changes.append(Change(ChangeKind.REMOVE, f"{STATUS_LINE_KEY}.command", "compositor removed"))
    created = _lifecycle_of(registry).get("created_status_line")
    if not [key for key in after if key != "type"]:
        notes.append(
            f"{STATUS_LINE_KEY} is removed entirely; it held nothing but this integration"
            if created
            else f"{STATUS_LINE_KEY} is removed entirely; no original command was ever recorded"
        )
        return None, changes, notes
    notes.append(
        f"no original command was recorded, so {STATUS_LINE_KEY} is left without one and no "
        "statusline will render; its other settings are preserved rather than guessed at"
    )
    return after, changes, notes


def plan_remove(
    document: SettingsDocument,
    registry: RegistryDocument,
    *,
    providers: tuple[str, ...],
    operation: str = "disable",
) -> Plan:
    """What removing these providers would change, without changing anything.

    The slot is given back only when the last provider goes, which is what makes
    disabling one product a registry-only operation that cannot disturb the
    others or the user's own statusline.
    """
    ownership = classify(document)
    refusal, remediation = _remove_refusal(ownership, registry)
    if refusal is not None:
        return Plan(
            operation=operation,
            settings_path=document.path,
            ownership=ownership,
            changes=(Change(ChangeKind.BLOCKED_UNKNOWN, STATUS_LINE_KEY, refusal),),
            fingerprint=document.fingerprint,
            registry_path=registry.path,
            registry_fingerprint=registry.fingerprint,
            refusal=refusal,
            remediation=remediation,
        )

    registered = _provider_ids(registry)
    removing = tuple(provider for provider in providers if provider in registered)
    remaining = tuple(provider for provider in registered if provider not in removing)
    changes: list[Change] = []
    notes: list[str] = [
        f"{provider} is not registered; nothing to remove"
        for provider in providers
        if provider not in registered
    ]
    for provider in removing:
        changes.append(
            Change(ChangeKind.REMOVE, f"registry.providers[{provider}]", "provider unregistered")
        )

    settings_after: dict | None = None
    state_to_remove: tuple[pathlib.Path, ...] = ()
    if remaining:
        entries = [
            entry
            for entry in (registry.data or {}).get("providers", [])
            if not (isinstance(entry, dict) and entry.get("provider") in removing)
        ]
        registry_after: dict | None = dict(registry.data or {}) | {"providers": entries}
        changes.extend(_preservation_changes(document, remaining))
        if ownership is not Ownership.HORONOM_OWNED:
            notes.append(
                "the statusline slot is not currently the compositor's, so only the registry "
                "changes here"
            )
    else:
        registry_after = None
        if registry.present:
            state_to_remove = (registry.path,)
            changes.append(
                Change(ChangeKind.REMOVE, str(registry.path), "the last provider is gone with it")
            )
        before = document.status_line
        if isinstance(before, dict):
            released, release_changes, release_notes = _released_status_line(
                before, ownership, registry
            )
            changes.extend(release_changes)
            notes.extend(release_notes)
            candidate = dict(document.data)
            if released is None:
                candidate.pop(STATUS_LINE_KEY, None)
            else:
                candidate[STATUS_LINE_KEY] = released
            settings_after = candidate
            changes.extend(
                _preservation_changes(document, (), include_status_line=released is not None)
            )

    return Plan(
        operation=operation,
        settings_path=document.path,
        ownership=ownership,
        changes=tuple(changes),
        fingerprint=document.fingerprint,
        registry_path=registry.path,
        registry_fingerprint=registry.fingerprint,
        settings_after=None if settings_after == document.data else settings_after,
        registry_after=None if registry_after == registry.data else registry_after,
        state_to_remove=state_to_remove,
        notes=tuple(notes),
    )


def _command_summary(status_line: object) -> dict:
    """Identify the configured command without reproducing it.

    The basename is reported and the full command is not. A user asking "what
    owns my statusline" is answered by `statusline-dogfood.sh`; the directory it
    sits in adds nothing they do not already know, and this output is exactly the
    kind of thing that gets pasted into an issue.
    """
    command = status_line.get("command") if isinstance(status_line, dict) else None
    if not isinstance(command, str) or not command.strip():
        return {"configured": False, "name": None, "is_compositor": False}
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return {
        "configured": True,
        "name": pathlib.Path(tokens[0]).name if tokens else None,
        "is_compositor": refers_to_compositor(command),
    }


def _drift(ownership: Ownership, registry: RegistryDocument) -> tuple[bool, str | None]:
    """Whether the world has moved away from the state this lifecycle last left.

    Reported, never corrected. Detecting drift and quietly re-taking the slot
    would make an upgrade indistinguishable from an install, which is the one
    behaviour a user who deliberately changed their statusline would experience
    as the tool fighting them.
    """
    if ownership is Ownership.DRIFTED:
        return True, "the statusLine carries a Horonom marker but runs a different command"
    if ownership is Ownership.ADOPTABLE:
        return True, "the statusLine runs the compositor but carries no ownership marker"
    if ownership is Ownership.UNSUPPORTED_SHAPE:
        return True, "the statusLine is a shape this version does not understand"
    if registry.present and not registry.usable:
        return True, f"the provider registry is unusable ({registry.problem})"
    if ownership is Ownership.HORONOM_OWNED and not registry.present:
        return True, "the compositor owns the statusline but no provider registry is present"
    if ownership is not Ownership.HORONOM_OWNED and _provider_ids(registry):
        return True, "providers are registered but the compositor does not own the statusline"
    return False, None


def _doctor_providers(registry: RegistryDocument, home: pathlib.Path | None, probe: bool) -> list[dict]:
    """One report line per registered provider, optionally by actually asking it.

    Probing runs each provider exactly as the statusline would, which means it
    may answer from -- and refresh -- Horonom's own provider cache. It never
    touches host configuration, and it reports availability rather than the
    rendered text, because a diagnostic that showed the line would invite reading
    presentation problems as provider problems.
    """
    if not registry.usable or not registry.data:
        return []
    parsed = compositor.parse_registry(registry.data)
    reports = []
    for entry in parsed.providers:
        report = {
            "provider": entry.provider,
            "scope": entry.scope.value,
            "timeout_ms": entry.timeout_ms,
            "command_name": pathlib.Path(entry.argv[0]).name,
        }
        if probe:
            status = compositor.run_provider(entry, entry.timeout_ms, home)
            report["availability"] = status.availability.value
            report["segments"] = len(status.segments)
        reports.append(report)
    return reports


def doctor(
    settings_path: str | os.PathLike | None = None,
    home: pathlib.Path | None = None,
    *,
    probe: bool = False,
) -> dict:
    """A read-only account of who owns the statusline and what would change it.

    Answers the questions a user actually has when the statusline is not what
    they expect, and mutates nothing on any path. What it deliberately does not
    do is print their configuration: unrelated keys are counted, the configured
    command is named but not reproduced, and no value from the settings file
    reaches the output. That file holds credentials in its `env` block on this
    very workstation.

    Claude Code's support for a single `statusLine.command` is reported as the
    premise it is, not as something discovered at runtime -- this module only
    ever writes into a Claude Code settings file, so there is nothing to detect.
    """
    path = pathlib.Path(settings_path or DEFAULT_SETTINGS_PATH).expanduser()
    registry = read_registry(home)
    report: dict = {
        "host": {
            "tool": "claude-code",
            "capability": "statusLine",
            "supported": True,
            "settings_path": str(path),
            "scope": settings_scope(path),
        },
        "registry_path": str(registry.path),
    }

    try:
        document = read_settings(path)
    except (SettingsParseError, LifecycleError) as exc:
        report["settings"] = {"exists": path.exists(), "readable": False, "problem": str(exc)}
        report["slot"] = {"owner": Ownership.UNSUPPORTED_SHAPE.value, "horonom_owned": False}
        report["drift"] = {"detected": True, "reason": str(exc)}
        report["providers"] = _doctor_providers(registry, home, probe)
        report["remediation"] = [
            f"repair {path} by hand; no lifecycle operation will write to it until it parses"
        ]
        return report

    ownership = classify(document)
    owned = ownership is Ownership.HORONOM_OWNED
    detected, reason = _drift(ownership, registry)
    report["settings"] = {
        "exists": document.existed,
        "readable": True,
        "problem": None,
        "unrelated_key_count": len([key for key in document.data if key != STATUS_LINE_KEY]),
    }
    report["slot"] = {
        "owner": ownership.value,
        "horonom_owned": owned,
        "command": _command_summary(document.status_line),
    }
    report["upstream"] = {
        "recorded": _upstream_of(registry) is not None,
        "name": pathlib.Path(shlex.split(_upstream_of(registry))[0]).name
        if _upstream_of(registry)
        else None,
        "created_status_line": _lifecycle_of(registry).get("created_status_line"),
    }
    report["providers"] = _doctor_providers(registry, home, probe)
    report["drift"] = {"detected": detected, "reason": reason}
    report["mutation_outlook"] = {
        "enabling_another_provider_changes_settings": not owned,
        "disabling_one_provider_changes_settings": owned and len(_provider_ids(registry)) == 1,
    }
    report["remediation"] = list(_remediation(ownership, registry, owned))
    return report


def _remediation(ownership: Ownership, registry: RegistryDocument, owned: bool) -> tuple[str, ...]:
    """What to do next, where there is something to do and it is safe to say so."""
    if ownership is Ownership.UNSUPPORTED_SHAPE:
        return (f"inspect {STATUS_LINE_KEY} in the settings file by hand",)
    if ownership is Ownership.DRIFTED:
        return (
            "the statusline was changed outside this lifecycle; `enable --adopt` takes it back "
            "and abandons the recorded original, `uninstall` removes the stale marker and leaves "
            "the current command alone",
        )
    if ownership is Ownership.ADOPTABLE:
        return ("`enable --adopt` records ownership of the configuration already in place",)
    if registry.present and not registry.usable:
        return (f"repair or remove {registry.path} by hand",)
    if owned and not _provider_ids(registry):
        return ("no providers are registered, so the statusline renders only the original line",)
    return ()


EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAILED = 3


def _describe(plan: Plan, result: ApplyResult | None) -> str:
    """The plan as prose, for a reader who is about to trust it with their config."""
    lines = [f"{plan.operation}: {plan.settings_path} ({plan.scope} scope)"]
    lines.append(f"  current owner: {plan.ownership.value}")
    if plan.refusal is not None:
        lines.append(f"  REFUSED: {plan.refusal}")
        lines.extend(f"  next: {step}" for step in plan.remediation)
        return "\n".join(lines)
    lines.append(f"  would change anything: {'yes' if plan.mutates else 'no'}")
    lines.append("  requires administrator authorization: no")
    for change in plan.changes:
        lines.append(f"  [{change.kind.value}] {change.target}: {change.detail}")
    lines.extend(f"  removed: {path}" for path in plan.state_to_remove)
    lines.extend(f"  note: {note}" for note in plan.notes)
    if result is not None:
        lines.append(
            f"  applied: settings_written={result.settings_written} "
            f"registry_written={result.registry_written} read_back_verified={result.verified}"
        )
    return "\n".join(lines)


def _describe_doctor(report: dict) -> str:
    """The doctor report as prose, in the order a confused user asks the questions."""
    host, slot = report["host"], report["slot"]
    lines = [
        f"host: {host['tool']} {host['capability']} supported={host['supported']}",
        f"settings: {host['settings_path']} ({host['scope']} scope)",
    ]
    settings = report["settings"]
    if not settings["readable"]:
        lines.append(f"  unreadable: {settings['problem']}")
    else:
        lines.append(f"  unrelated top-level keys, untouched: {settings['unrelated_key_count']}")
    command = slot.get("command") or {}
    lines.append(
        f"statusline owner: {slot['owner']} (command={command.get('name')}, "
        f"horonom={slot['horonom_owned']})"
    )
    upstream = report.get("upstream") or {}
    lines.append(
        f"original statusline: recorded={upstream.get('recorded')} name={upstream.get('name')}"
    )
    providers = report["providers"]
    lines.append(f"providers registered: {len(providers)}")
    for provider in providers:
        detail = f"  - {provider['provider']} [{provider['scope']}] {provider['command_name']}"
        if "availability" in provider:
            detail += f" -> {provider['availability']} ({provider['segments']} segment(s))"
        lines.append(detail)
    drift = report["drift"]
    lines.append(f"drift: {'yes -- ' + drift['reason'] if drift['detected'] else 'none'}")
    outlook = report.get("mutation_outlook")
    if outlook:
        lines.append(
            "enabling another provider would change settings: "
            f"{outlook['enabling_another_provider_changes_settings']}"
        )
        lines.append(
            "disabling one provider would change settings: "
            f"{outlook['disabling_one_provider_changes_settings']}"
        )
    lines.extend(f"next: {step}" for step in report["remediation"])
    return "\n".join(lines)


def _describe_list(report: dict) -> str:
    """Just the registrations, for the question "what is turned on"."""
    lines = [f"statusline owner: {report['slot']['owner']}"]
    if not report["providers"]:
        lines.append("no providers registered")
    lines.extend(
        f"  - {provider['provider']} [{provider['scope']}]" for provider in report["providers"]
    )
    if report["drift"]["detected"]:
        lines.append(f"drift: {report['drift']['reason']}")
    return "\n".join(lines)


def _settings_path(argument: str | None) -> pathlib.Path:
    return pathlib.Path(argument or DEFAULT_SETTINGS_PATH).expanduser()


def _emit(plan: Plan, result: ApplyResult | None, as_json: bool, stream) -> int:
    payload = plan.to_json()
    if result is not None:
        payload["applied"] = result.to_json()
    print(json.dumps(payload, indent=2) if as_json else _describe(plan, result), file=stream)
    return EXIT_REFUSED if plan.refusal is not None else EXIT_OK


def _run_plan(plan: Plan, options: argparse.Namespace, stream) -> int:
    """Apply a plan unless this is a dry run, and report either way.

    A refusal is reported and exits non-zero rather than raising: the caller is a
    person or a script that needs the reason, and a traceback is not a reason.
    """
    if plan.refusal is not None or options.dry_run:
        return _emit(plan, None, options.json, stream)
    try:
        result = apply(plan)
    except (ConcurrentModificationError, VerificationError, LifecycleError) as exc:
        print(f"{plan.operation} failed: {exc}", file=stream)
        return EXIT_FAILED
    return _emit(plan, result, options.json, stream)


def build_parser() -> argparse.ArgumentParser:
    """The command line, which exists so that nobody edits settings.json by hand.

    Every mutating subcommand accepts `--dry-run`, and the plan it prints is the
    same object `apply` would act on -- not a rehearsal of it. A preview that is
    generated by different code from the mutation is a preview of nothing.
    """
    parser = argparse.ArgumentParser(
        prog="statusline",
        description="Take, share and give back Claude Code's single statusline slot.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def shared(subcommand: argparse.ArgumentParser, *, mutating: bool = True) -> None:
        subcommand.add_argument(
            "--settings",
            help=f"path to the Claude Code settings file (default {DEFAULT_SETTINGS_PATH})",
        )
        subcommand.add_argument("--json", action="store_true", help="emit machine-readable output")
        if mutating:
            subcommand.add_argument(
                "--dry-run", action="store_true", help="print the plan and change nothing"
            )

    enable = subcommands.add_parser("enable", help="register a provider, taking the slot if needed")
    enable.add_argument("--provider", required=True, help="provider id, e.g. fornax")
    enable.add_argument(
        "--scope",
        required=True,
        choices=[scope.value for scope in contract.Scope],
        help="what the provider's readings are about",
    )
    enable.add_argument(
        "--command",
        action="append",
        required=True,
        metavar="ARG",
        dest="command_argv",
        help="one argument of the provider command; repeat for each argument",
    )
    enable.add_argument("--timeout-ms", type=int, help="per-render budget for this provider")
    enable.add_argument(
        "--adopt",
        action="store_true",
        help="accept that no recoverable record of an original statusline exists",
    )
    shared(enable)

    disable = subcommands.add_parser("disable", help="unregister one provider")
    disable.add_argument("--provider", required=True)
    shared(disable)

    uninstall = subcommands.add_parser(
        "uninstall", help="unregister every provider and give the slot back"
    )
    shared(uninstall)

    listing = subcommands.add_parser("list", help="show what is registered")
    shared(listing, mutating=False)

    diagnose = subcommands.add_parser("doctor", help="explain who owns the statusline")
    diagnose.add_argument(
        "--probe", action="store_true", help="also run each provider and report what it answered"
    )
    shared(diagnose, mutating=False)
    return parser


def main(argv: list[str] | None = None, stdout: object = None) -> int:
    options = build_parser().parse_args(argv)
    stream = stdout if stdout is not None else sys.stdout
    path = _settings_path(options.settings)

    if options.command in ("doctor", "list"):
        report = doctor(path, probe=getattr(options, "probe", False))
        if options.command == "list":
            # `.get`, because an unparseable settings file yields a report with no
            # `upstream` section at all -- and that is precisely the state a user
            # runs this command to understand, so it must not be the state that
            # raises.
            report = {key: report.get(key) for key in ("slot", "upstream", "providers", "drift")}
            print(json.dumps(report, indent=2) if options.json else _describe_list(report), file=stream)
            return EXIT_OK
        print(json.dumps(report, indent=2) if options.json else _describe_doctor(report), file=stream)
        return EXIT_REFUSED if report["drift"]["detected"] else EXIT_OK

    try:
        document = read_settings(path)
    except LifecycleError as exc:
        # Includes the malformed-JSON case, which is the one that matters: the
        # operation stops here, before a plan exists, so there is no code path
        # from an unparseable settings file to a write.
        print(f"{options.command} refused: {exc}", file=stream)
        return EXIT_REFUSED
    registry = read_registry()

    if options.command == "enable":
        try:
            registration = ProviderRegistration(
                provider=options.provider,
                argv=tuple(options.command_argv),
                scope=options.scope,
                timeout_ms=options.timeout_ms,
            )
        except (LifecycleError, contract.ContractViolation, ValueError) as exc:
            print(f"enable refused: {exc}", file=stream)
            return EXIT_REFUSED
        plan = plan_enable(document, registry, registration, adopt=options.adopt)
    elif options.command == "disable":
        plan = plan_remove(document, registry, providers=(options.provider,), operation="disable")
    else:
        plan = plan_remove(
            document, registry, providers=_provider_ids(registry), operation="uninstall"
        )
    return _run_plan(plan, options, stream)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
