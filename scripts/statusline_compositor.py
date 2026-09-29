"""The single statusline command Horonom owns, and everything it multiplexes.

Claude Code gives a scope exactly one `statusLine.command`. Several Horonom
products want to say something there, and the user usually already has their own
script in that slot. This module is what makes those compatible: it becomes the
one configured command, runs the user's original command as an *upstream*
provider, runs the registered Horonom providers beside it, and prints the
original output followed by a bounded Horonom block.

The invariants, in the order they matter:

1. **The user's line is not ours.** Their configured command string is passed to
   the shell unmodified, receives the same stdin bytes we were given, and its
   stdout is reproduced verbatim as the prefix of our output. It is never
   edited, parsed, rewritten, reordered, shortened or annotated, and no failure
   of ours may suppress it. "No upstream" is a valid state, not an error.
2. **Nothing expensive happens here.** This runs on every statusline refresh.
   No LLM call, no network request, no install, no build, no daemon start, no
   filesystem walk. Providers are subprocesses with a per-provider timeout under
   a bounded overall deadline, run concurrently, so the wall clock is roughly
   the slowest single provider rather than the sum of the timeouts.
3. **A failure is reported, not hidden.** A provider that times out, crashes,
   emits unparseable output or violates the contract becomes an explicit
   not-available status. It never becomes silence, and it never becomes a
   healthy zero — `statusline_contract` refuses that shape outright.
4. **We do not read the payload.** The JSON the host writes to our stdin is
   consumed as opaque bytes and forwarded. Not parsing it means there is no
   schema of the host's to track, and nothing from it can leak into a rendered
   label by accident.

Config *mutation* is deliberately not here: this module only reads the registry
that the install lifecycle writes (HORO-1566). It creates its own cache
directory and nothing else.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import statusline_contract as contract
import statusline_render as render

# Horonom-owned state. Everything this module writes lives under here, and it
# writes nothing anywhere else — in particular nothing under the host's own
# configuration directory, which it has no ownership of.
STATE_HOME_ENV = "HORONOM_STATUSLINE_HOME"
DEFAULT_STATE_HOME = "~/.horonom/statusline"
REGISTRY_FILENAME = "registry.json"
CACHE_DIRNAME = "cache"

# Set on every child process. A registry whose upstream points back at this
# command would otherwise fork bomb the host one refresh at a time.
DEPTH_ENV = "HORONOM_STATUSLINE_DEPTH"
MAX_DEPTH = 1

REGISTRY_VERSION = 1
SUPPORTED_REGISTRY_VERSIONS = frozenset({REGISTRY_VERSION})

# Bounds, not preferences. The registry may lower any of these; it may not raise
# them past the maximum, because a statusline that takes a visible moment to
# appear is a regression in the host's own responsiveness and the user did not
# consent to that by enabling a status provider.
DEFAULT_PROVIDER_TIMEOUT_MS = 250
MAX_PROVIDER_TIMEOUT_MS = 2000
DEFAULT_DEADLINE_MS = 600
MAX_DEADLINE_MS = 3000
DEFAULT_UPSTREAM_TIMEOUT_MS = 1500
MAX_UPSTREAM_TIMEOUT_MS = 5000
MAX_PROVIDERS = 8
MAX_ARGV_LENGTH = 16

# How long a killed child's process group is given to release our stdout pipe.
# Short by design: at this point we are only trying to collect output that was
# already written, not waiting for anything to finish.
KILL_GRACE_SECONDS = 0.1

# Output caps. A provider that streams megabytes is a defect; truncating its
# stdout before parsing keeps that defect from becoming a memory problem in the
# host's render path.
MAX_PROVIDER_OUTPUT_BYTES = 64 * 1024
MAX_UPSTREAM_OUTPUT_BYTES = 256 * 1024


class RegistryError(ValueError):
    """The registry is missing, unreadable, or not a shape we recognise.

    Always fatal for the Horonom block and never fatal for the user's line: the
    two failure modes are handled separately in `main` precisely so a mistake in
    our own state cannot take their statusline down with it.
    """


@dataclasses.dataclass(frozen=True)
class ProviderEntry:
    """One registered provider, as the install lifecycle recorded it.

    `scope` is metadata the product declared at enable time. It is used *only*
    to render a not-available status, where there is no live answer to take a
    scope from. A provider that answers supplies its own, and that one wins.
    """

    provider: str
    argv: tuple[str, ...]
    scope: contract.Scope
    timeout_ms: int


@dataclasses.dataclass(frozen=True)
class Registry:
    """The read model of Horonom-owned statusline state.

    `upstream_command` is the command string that occupied `statusLine.command`
    before Horonom took the slot, stored exactly as the host had it. `None` means
    there was no original statusline, which is an ordinary state.
    """

    upstream_command: str | None
    upstream_timeout_ms: int
    providers: tuple[ProviderEntry, ...]
    mode: render.PresentationMode
    width_budget: int | None
    deadline_ms: int


def state_home() -> pathlib.Path:
    """Where Horonom keeps its own statusline state."""
    override = os.environ.get(STATE_HOME_ENV)
    root = pathlib.Path(override) if override else pathlib.Path(DEFAULT_STATE_HOME)
    return root.expanduser()


def registry_path(home: pathlib.Path | None = None) -> pathlib.Path:
    return (home or state_home()) / REGISTRY_FILENAME


def cache_dir(home: pathlib.Path | None = None) -> pathlib.Path:
    return (home or state_home()) / CACHE_DIRNAME


def _bounded_ms(payload: dict, key: str, default: int, maximum: int) -> int:
    """Read a millisecond budget, clamped rather than trusted."""
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryError(f"{key} must be an integer number of milliseconds")
    if value < 1:
        raise RegistryError(f"{key} must be at least 1ms")
    return min(value, maximum)


def _require_argv(value: object, provider: str) -> tuple[str, ...]:
    """Validate a provider's command as an argument vector.

    A list, never a string: providers are Horonom-owned commands, so they are
    executed directly with no shell between us and them. That removes a whole
    injection surface, and costs nothing because we are the ones who wrote the
    entry. The user's own command is the deliberate exception — see
    `run_upstream`.
    """
    if not isinstance(value, list) or not value:
        raise RegistryError(f"provider {provider!r}: command must be a non-empty list")
    if len(value) > MAX_ARGV_LENGTH:
        raise RegistryError(f"provider {provider!r}: command has too many arguments")
    for part in value:
        if not isinstance(part, str) or not part:
            raise RegistryError(f"provider {provider!r}: command entries must be non-empty strings")
    return tuple(value)


def _require_scope(value: object, provider: str) -> contract.Scope:
    """Parse a declared scope strictly.

    No lenient fallback: host-wide state rendered as session-scoped tells the
    user something false about which of their sessions a provider speaks for.
    """
    try:
        return contract.Scope(value)
    except ValueError as exc:
        raise RegistryError(f"provider {provider!r}: {value!r} is not a known scope") from exc


def _parse_provider(payload: object) -> ProviderEntry | None:
    """Parse one registry entry, or `None` if it is registered but disabled."""
    if not isinstance(payload, dict):
        raise RegistryError("each provider entry must be an object")
    provider = contract.require_provider_id(payload.get("provider"))
    if payload.get("enabled", True) is not True:
        return None
    return ProviderEntry(
        provider=provider,
        argv=_require_argv(payload.get("command"), provider),
        scope=_require_scope(payload.get("scope"), provider),
        timeout_ms=_bounded_ms(
            payload, "timeout_ms", DEFAULT_PROVIDER_TIMEOUT_MS, MAX_PROVIDER_TIMEOUT_MS
        ),
    )


def parse_registry(payload: object) -> Registry:
    """Validate the registry document, refusing anything unfamiliar.

    Strict on purpose. This document decides what commands we execute and what
    the user's original statusline was; guessing at a shape we do not recognise
    risks either running the wrong thing or losing their line. An unsupported
    version is refused rather than best-efforted, because a future writer may
    have moved the very field we would be reading.
    """
    if not isinstance(payload, dict):
        raise RegistryError("registry must be a JSON object")
    version = payload.get("registry_version")
    if version not in SUPPORTED_REGISTRY_VERSIONS:
        raise RegistryError(
            f"registry_version {version!r} is not supported by this compositor "
            f"(supported: {sorted(SUPPORTED_REGISTRY_VERSIONS)})"
        )

    upstream = payload.get("upstream")
    upstream_command: str | None = None
    if upstream is not None:
        if not isinstance(upstream, dict):
            raise RegistryError("upstream must be an object or absent")
        command = upstream.get("command")
        if command is not None and (not isinstance(command, str) or not command.strip()):
            raise RegistryError("upstream.command must be a non-empty string or absent")
        upstream_command = command

    entries = payload.get("providers", [])
    if not isinstance(entries, list):
        raise RegistryError("providers must be a list")
    if len(entries) > MAX_PROVIDERS:
        raise RegistryError(f"at most {MAX_PROVIDERS} providers may be registered")
    providers = tuple(entry for entry in map(_parse_provider, entries) if entry is not None)
    identifiers = [entry.provider for entry in providers]
    if len(identifiers) != len(set(identifiers)):
        raise RegistryError("provider ids must be unique in the registry")

    presentation = payload.get("presentation", {})
    if not isinstance(presentation, dict):
        raise RegistryError("presentation must be an object")
    width = presentation.get("width_budget")
    if width is not None and (isinstance(width, bool) or not isinstance(width, int) or width < 1):
        raise RegistryError("presentation.width_budget must be a positive integer or absent")

    return Registry(
        upstream_command=upstream_command,
        upstream_timeout_ms=_bounded_ms(
            payload, "upstream_timeout_ms", DEFAULT_UPSTREAM_TIMEOUT_MS, MAX_UPSTREAM_TIMEOUT_MS
        ),
        providers=providers,
        # Unlike everything else here, an unrecognised mode falls back instead of
        # refusing: it decides how the line looks, not what we execute, and
        # losing the whole statusline over a typo in a cosmetic preference is a
        # worse outcome than rendering it in the default style.
        mode=render.PresentationMode.parse(presentation.get("mode")),
        width_budget=width,
        deadline_ms=_bounded_ms(payload, "deadline_ms", DEFAULT_DEADLINE_MS, MAX_DEADLINE_MS),
    )


def load_registry(path: pathlib.Path | None = None) -> Registry:
    """Read and validate the registry from disk."""
    target = path or registry_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RegistryError(f"no registry at {target}") from exc
    except OSError as exc:
        # Deliberately reports the errno class and not the message, which on
        # some platforms carries the full path of every parent directory.
        raise RegistryError(f"registry at {target} could not be read ({exc.errno})") from exc
    try:
        return parse_registry(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry at {target} is not valid JSON (line {exc.lineno})") from exc


if __name__ == "__main__":
    sys.exit(main())
