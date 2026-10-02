"""Coexistence with external managers that own the host tool's settings file.

Why this module exists (HORO-1660): `~/.claude/settings.json` is shared, and
Horonom is not always the only tool writing it. A *configuration manager* --
a tool that stores its own copy of the whole document and writes that copy over
the live file -- will silently drop every key it does not itself model, including
the `statusLine` this product installs. That is not a hypothetical: the observed
case replayed a week-old whole-document snapshot over the live file on startup
and took the Horonom statusline, the `hooks` block and five environment keys with
it.

The shape of the answer matters as much as the answer. Two things are explicitly
not done here:

* **No polling.** A loop that rewrites the live file whenever it changes would
  put two tools in a permanent overwrite fight, and the user would see a
  statusline that flickers between owners. Seeding happens at the lifecycle
  moments the operator already asked for (enable, remove) and nowhere else.
* **No whole-file restore.** This module never writes a remembered document over
  a current one. Every write is a patch of exactly the `statusLine` key inside a
  document that is otherwise passed through untouched, which is the same
  ownership rule `statusline_lifecycle` applies to the live file (ADR-0009).

What it does instead is seed the product's own key into the manager's *canonical
representations* -- the stored documents the manager will later write to live --
so that when the manager reapplies, the document it writes already carries the
statusline. The manager keeps full ownership of its own fields; this module
touches one key and reads nothing it does not need.

The seed value always comes from one place, the caller's computed `statusLine`
object, so that several seeded copies cannot drift into disagreeing versions.
`divergence` is what proves that, and is reported by `doctor` rather than being
assumed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import pathlib
import plistlib
import sqlite3

STATUS_LINE_KEY = "statusLine"

# Mirrors `statusline_lifecycle.MARKER_KEY`. Duplicated rather than imported
# because the lifecycle imports this module, and the only thing needed here is the
# ability to recognise a statusline as this product's before handing it back --
# unseeding a statusline that is not ours is precisely the clobber to avoid.
MARKER_KEY = "_horonom"

# The app whose write semantics are modelled here. Named explicitly rather than
# detected generically because "a tool that replaces the whole document" is not
# something that can be inferred from a config file -- it takes reading the
# implementation, and the conclusions below are specific to one of them.
CC_SWITCH = "cc-switch"

# Where CC-Switch keeps its single source of truth, and the two tables inside it
# that hold documents destined for the live file.
_CC_SWITCH_STORE = "~/.cc-switch/cc-switch.db"
_CC_SWITCH_BUNDLE = "/Applications/CC Switch.app/Contents/Info.plist"

# The host application whose configuration this module is about. CC-Switch
# manages several; rows for the others are not ours to read or touch.
_CLAUDE_APP_TYPE = "claude"

# The literal CC-Switch writes in place of a credential while it proxies. Matched
# as evidence that it is actively managing the file, and deliberately never
# emitted: it is a placeholder rather than a secret, but a diagnostic that prints
# whatever it found in a credential field is one upstream change away from
# printing a real one.
_PROXY_PLACEHOLDER = "PROXY_MANAGED"

# How long to wait for the manager's own writer before giving up. A manager that
# is mid-write holds the database briefly; failing instantly would turn an
# ordinary race into a reported error, and waiting forever would hang an install.
_BUSY_TIMEOUT_SECONDS = 5.0

# The two representation kinds, and the row shape each lives in. `key_column`
# identifies the row; `column` holds a JSON document whose `statusLine` is the
# only key this module will touch.
_CC_SWITCH_REPRESENTATIONS = (
    # Written to live verbatim on every provider switch.
    ("profile", "providers", "settings_config", "id"),
    # Replayed over live wholesale when the manager decides it exited abnormally.
    # This is the one that actually fired.
    ("pre_takeover_snapshot", "proxy_live_backup", "original_config", "app_type"),
)


class ExternalOwnerError(Exception):
    """A seed or unseed could not be carried out.

    Separate from `statusline_lifecycle.LifecycleError` because the two have
    different consequences: a lifecycle failure means the statusline is not
    installed, while this one means it is installed but will not survive the
    external manager's next write. The caller reports that distinction rather
    than collapsing both into "failed".
    """


@dataclasses.dataclass(frozen=True)
class Representation:
    """One stored document an external manager will later write over the live file.

    `fingerprint` is of the stored text rather than the parsed value, because two
    different texts parse to the same value and only the text can answer "did
    this row change under us" between planning and writing.
    """

    kind: str
    locator: str
    table: str
    column: str
    key_column: str
    key: str
    fingerprint: str
    data: dict

    @property
    def status_line(self) -> object:
        return self.data.get(STATUS_LINE_KEY)

    def to_json(self) -> dict:
        """Structural facts only: no stored values, which include credentials."""
        return {
            "kind": self.kind,
            "locator": self.locator,
            "key_count": len(self.data),
            "status_line_present": isinstance(self.status_line, dict),
        }


@dataclasses.dataclass(frozen=True)
class ExternalOwner:
    """An external manager of the host tool's settings file, as detected.

    `supported` is false for a store this module can see but does not recognise
    the shape of. That is reported, never worked around: guessing at an unknown
    schema is how a tool corrupts another tool's database, and the honest outcome
    is a statusline that works now and a warning that it may not survive.
    """

    name: str
    version: str | None
    store: pathlib.Path
    representations: tuple[Representation, ...]
    evidence: tuple[str, ...]
    supported: bool
    problem: str | None = None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "store": str(self.store),
            "supported": self.supported,
            "problem": self.problem,
            "evidence": list(self.evidence),
            "representations": [item.to_json() for item in self.representations],
        }


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cc_switch_version() -> str | None:
    """The installed manager's version, for reporting which semantics apply.

    Read from the bundle rather than the database because the database has no
    version row, and the write semantics that matter here differ between releases
    -- the version is the only thing that tells a reader of a report whether the
    destructive paths this module compensates for are still present.
    """
    try:
        with open(_CC_SWITCH_BUNDLE, "rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return None
    version = plist.get("CFBundleShortVersionString")
    return version if isinstance(version, str) else None


@contextlib.contextmanager
def _connect(store: pathlib.Path, *, writable: bool):
    """A connection to the manager's store.

    Read-only connections are opened immutable so that inspecting a running
    application's database cannot modify it even by replaying its journal. Write
    connections carry a busy timeout instead, because the manager may legitimately
    hold the database for the moment it takes to write its own row.
    """
    if writable:
        connection = sqlite3.connect(
            str(store), timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
    else:
        connection = sqlite3.connect(f"file:{store}?mode=ro&immutable=1", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _read_representations(connection: sqlite3.Connection) -> tuple[list[Representation], str | None]:
    """Every stored document for this host tool, or why they could not be read."""
    found: list[Representation] = []
    for kind, table, column, key_column in _CC_SWITCH_REPRESENTATIONS:
        columns = _columns(connection, table)
        if not columns:
            return [], f"table {table} is missing"
        required = {column, key_column, "app_type"}
        if not required.issubset(columns):
            missing = ", ".join(sorted(required - columns))
            return [], f"table {table} is missing column(s): {missing}"
        # Ordered so that the ordinal in a locator means the same row across two
        # runs. The row key itself is not used in the locator: these are provider
        # identifiers a user chose, and a locator ends up in bug reports.
        rows = connection.execute(
            f"SELECT {key_column}, {column} FROM {table} WHERE app_type = ? "
            f"ORDER BY {key_column}",
            (_CLAUDE_APP_TYPE,),
        ).fetchall()
        for ordinal, (key, stored) in enumerate(rows, start=1):
            if not isinstance(stored, str) or not stored.strip():
                continue
            try:
                data = json.loads(stored)
            except json.JSONDecodeError:
                # A document the manager itself would fail to use. Skipped rather
                # than refused: it cannot carry our statusline to the live file,
                # so it is not a row whose state we can improve.
                continue
            if not isinstance(data, dict):
                continue
            found.append(
                Representation(
                    kind=kind,
                    locator=f"{table}.{column}[{kind} {ordinal}]",
                    table=table,
                    column=column,
                    key_column=key_column,
                    key=str(key),
                    fingerprint=_fingerprint(stored),
                    data=data,
                )
            )
    return found, None


def _takeover_evidence(settings: dict) -> str | None:
    """Whether the manager is currently proxying this host tool.

    Reported as a fact about the shape of the document, never by echoing the
    value that proves it.
    """
    env = settings.get("env")
    if not isinstance(env, dict):
        return None
    if any(value == _PROXY_PLACEHOLDER for value in env.values()):
        return "live credentials are the manager's proxy placeholder"
    return None


def detect(live_settings: dict | None = None, *, home: pathlib.Path | None = None) -> ExternalOwner | None:
    """The external manager of the host settings file, or `None` if there is none.

    `live_settings` is the already-parsed live document, passed in rather than
    re-read so that detection cannot disagree with the document the caller is
    planning against.

    Absence is the ordinary case and is not an error: most installs have no
    external manager, and this returning `None` must leave the lifecycle exactly
    as it was before this module existed.
    """
    root = pathlib.Path(home) if home is not None else pathlib.Path.home()
    store = pathlib.Path(_CC_SWITCH_STORE.replace("~", str(root), 1)).expanduser()
    if not store.is_file():
        return None

    evidence = [f"store present at {store}"]
    if live_settings is not None:
        takeover = _takeover_evidence(live_settings)
        if takeover is not None:
            evidence.append(takeover)

    version = _cc_switch_version()
    try:
        with _connect(store, writable=False) as connection:
            representations, problem = _read_representations(connection)
    except sqlite3.Error as exc:
        # The class name, not the message: SQLite error text can quote row
        # content, and these rows hold credentials.
        return ExternalOwner(
            name=CC_SWITCH,
            version=version,
            store=store,
            representations=(),
            evidence=tuple(evidence),
            supported=False,
            problem=f"the store could not be read ({type(exc).__name__})",
        )

    if problem is not None:
        return ExternalOwner(
            name=CC_SWITCH,
            version=version,
            store=store,
            representations=(),
            evidence=tuple(evidence),
            supported=False,
            problem=f"the store has an unrecognised shape: {problem}",
        )

    return ExternalOwner(
        name=CC_SWITCH,
        version=version,
        store=store,
        representations=tuple(representations),
        evidence=tuple(evidence),
        supported=True,
    )


@dataclasses.dataclass(frozen=True)
class SeedPlan:
    """What seeding or unseeding would change in the manager's own store.

    Carries the fingerprint of every row it was formed against, for the same
    reason a lifecycle plan carries the settings file's: applying it to a row that
    has since moved is the clobber this is all here to prevent.
    """

    owner: ExternalOwner
    operation: str
    status_line: dict | None
    targets: tuple[Representation, ...]

    @property
    def mutates(self) -> bool:
        return bool(self.targets)

    def to_json(self) -> dict:
        return {
            "owner": self.owner.name,
            "operation": self.operation,
            "targets": [item.to_json() for item in self.targets],
        }


def _seeded(representation: Representation, status_line: dict | None) -> bool:
    """Whether this row already says exactly what a seed would make it say."""
    current = representation.status_line
    if status_line is None:
        return STATUS_LINE_KEY not in representation.data
    return current == status_line


def plan_seed(owner: ExternalOwner, status_line: dict) -> SeedPlan:
    """What it would take for the manager's own writes to carry this statusline.

    Rows that already agree are not targets, so running an install twice seeds
    nothing the second time -- the idempotence the lifecycle promises for the
    live file has to hold here too, or a repeated install would churn another
    tool's database.
    """
    targets = tuple(item for item in owner.representations if not _seeded(item, status_line))
    return SeedPlan(owner=owner, operation="seed", status_line=dict(status_line), targets=targets)


def plan_unseed(owner: ExternalOwner, upstream: dict | None) -> SeedPlan:
    """What it would take to hand the manager's stored documents back.

    `upstream` is the statusline that was there before this product took the slot,
    or `None` if there was none and the key should go away entirely. This is the
    `A+B+C -> A+C` invariant applied to the external store: removal puts back what
    was recorded, and never a document remembered from elsewhere.

    Every seeded row is handed the same value, including a row that had no
    statusline at all before it was seeded. That is a deliberate, documented
    inexactness, and the reasoning is worth stating because the alternatives are
    both worse. Nothing in the store can distinguish "we replaced a value here"
    from "we added the key here" after the fact, so exactness would need either a
    per-row flag inside the marker -- which then rides into the live file on the
    next profile switch and makes every comparison of ours special-case it -- or a
    second receipt of our own with its own way to go stale. What this choice costs
    is that a profile which showed no statusline before may show the user's own
    statusline afterwards; what it never does is destroy or overwrite anything,
    which is what the invariant is actually protecting.
    """
    targets = tuple(
        item
        for item in owner.representations
        if isinstance(item.status_line, dict)
        and item.status_line.get(MARKER_KEY) is not None
        and not _seeded(item, upstream)
    )
    return SeedPlan(
        owner=owner,
        operation="unseed",
        status_line=dict(upstream) if upstream is not None else None,
        targets=targets,
    )


def _patched(data: dict, status_line: dict | None) -> dict:
    """The stored document with only its `statusLine` changed.

    A copy of the original with one key replaced, rather than a document built
    from what this module knows about: every other key -- the manager's provider
    and model fields, and anything a newer version of it has added that this code
    has never heard of -- passes through by construction rather than by being
    enumerated.
    """
    patched = dict(data)
    if status_line is None:
        patched.pop(STATUS_LINE_KEY, None)
    else:
        patched[STATUS_LINE_KEY] = status_line
    return patched


def apply_seed(plan: SeedPlan) -> tuple[int, tuple[str, ...]]:
    """Carry out a seed plan, or refuse to.

    Every target row is re-read inside one immediate transaction and checked
    against the fingerprint the plan was formed against, so a row the manager
    changed in between aborts the whole operation with nothing written. All rows
    move together or none do: a half-seeded store would have some profiles
    carrying the statusline and others not, which is the divergence this is meant
    to make impossible.

    Returns how many rows were written and the locators that were, for
    disclosure.
    """
    if not plan.owner.supported:
        raise ExternalOwnerError(plan.owner.problem or "the external manager's store is unsupported")
    if not plan.mutates:
        return 0, ()

    written: list[str] = []
    try:
        with _connect(plan.owner.store, writable=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for target in plan.targets:
                    row = connection.execute(
                        f"SELECT {target.column} FROM {target.table} "
                        f"WHERE {target.key_column} = ? AND app_type = ?",
                        (target.key, _CLAUDE_APP_TYPE),
                    ).fetchone()
                    if row is None or not isinstance(row[0], str):
                        raise ExternalOwnerError(
                            f"{target.locator} is no longer present; nothing was written"
                        )
                    if _fingerprint(row[0]) != target.fingerprint:
                        raise ExternalOwnerError(
                            f"{target.locator} changed after this plan was formed; "
                            "nothing was written"
                        )
                    # Re-parsed from the row just read rather than reusing the
                    # value read at plan time, so the document written is a patch
                    # of what is actually stored now.
                    patched = _patched(json.loads(row[0]), plan.status_line)
                    connection.execute(
                        f"UPDATE {target.table} SET {target.column} = ? "
                        f"WHERE {target.key_column} = ? AND app_type = ?",
                        (json.dumps(patched), target.key, _CLAUDE_APP_TYPE),
                    )
                    written.append(target.locator)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
    except sqlite3.Error as exc:
        raise ExternalOwnerError(
            f"the external manager's store could not be written ({type(exc).__name__})"
        ) from exc

    _verify(plan)
    return len(written), tuple(written)


def _verify(plan: SeedPlan) -> None:
    """Read every seeded row back and refuse to call the seed a success unless it took.

    The same distinction `statusline_lifecycle` draws: "the update returned" and
    "the row now says what we intended" are different claims, and a report is only
    worth making about the second.
    """
    with _connect(plan.owner.store, writable=False) as connection:
        for target in plan.targets:
            row = connection.execute(
                f"SELECT {target.column} FROM {target.table} "
                f"WHERE {target.key_column} = ? AND app_type = ?",
                (target.key, _CLAUDE_APP_TYPE),
            ).fetchone()
            if row is None or not isinstance(row[0], str):
                raise ExternalOwnerError(f"{target.locator} could not be read back")
            landed = json.loads(row[0]).get(STATUS_LINE_KEY)
            if plan.status_line is None:
                if landed is not None:
                    raise ExternalOwnerError(f"{target.locator} still carries a statusline")
            elif landed != plan.status_line:
                raise ExternalOwnerError(
                    f"{target.locator} does not match the plan after writing it"
                )


def divergence(owner: ExternalOwner, status_line: dict) -> tuple[str, ...]:
    """Locators whose stored statusline disagrees with the one source of truth.

    The failure this exists to catch is the one where a statusline is copied into
    several profiles and they drift, so that switching provider silently changes
    which version of the product is in the line. Comparing every copy against the
    caller's single computed value is what makes that detectable instead of
    assumed.
    """
    return tuple(
        item.locator for item in owner.representations if not _seeded(item, status_line)
    )
