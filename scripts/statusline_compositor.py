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
import enum
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


class ColorPreference(enum.Enum):
    """What the user asked for, as distinct from what the terminal can do.

    `AUTO` is the default and means "decide from the environment". The three
    explicit values exist for the cases detection cannot get right from inside a
    captured process: a user whose terminal renders 256-colour sequences as
    literal text, and a user who wants the emphasis on a surface we would not
    have guessed.

    Unrecognised input resolves to `NEVER`, which is the opposite of how `mode`
    and `depth` fall back. Those two decide how the line *looks* and an
    unreadable line is worse than an ignored preference, so they fall back to a
    working default. Colour decides only emphasis on a line that is already
    complete in words, so the cost of falling back to "off" is nothing a reader
    needs, and the cost of falling back to "on" is `ESC[32m` printed literally
    into the prompt of whoever typoed it.
    """

    AUTO = "auto"
    NEVER = "never"
    ANSI16 = "ansi16"
    ANSI256 = "ansi256"

    @classmethod
    def parse(cls, value: object) -> "ColorPreference":
        if value is None:
            return cls.AUTO
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            name = value.strip().lower().replace("-", "_")
            for member in cls:
                if member.value == name:
                    return member
            # The spellings a reader would reasonably write for "off" and "on".
            # `always` resolves to the conservative rung rather than the rich one:
            # 16-colour sequences render on everything that renders colour at all.
            if name in ("no", "off", "false", "none"):
                return cls.NEVER
            if name in ("yes", "on", "true", "always", "ansi"):
                return cls.ANSI16
        return cls.NEVER


# The environment variables that decide capability, and what each one is evidence
# of. Kept here rather than inline because three of the four are conventions this
# module did not invent and a reader should be able to see all of them at once.
#
# `NO_COLOR` (https://no-color.org) is honoured whenever it is present and
# non-empty, ahead of the registry's own preference. It is the user's accessibility
# switch and the registry is a per-install cosmetic setting; a reader who exported
# `NO_COLOR` because escape sequences are unreadable to them is not asking for a
# per-product exception. `TERM=dumb` is honoured the same way for a simpler reason:
# it is not a preference at all, it is the terminal saying it cannot.
NO_COLOR_ENV = "NO_COLOR"
TERM_ENV = "TERM"
COLORTERM_ENV = "COLORTERM"
# Claude Code sets this in the statusline command's environment. It is the one
# piece of evidence that makes a non-TTY destination still worth colouring: the
# host captures our stdout -- so `isatty` is always false here -- and renders what
# we return into a terminal that does take escapes. Without this, the correct
# answer for a captured stream is "no colour", and the statusline would be
# permanently monochrome in exactly the surface this feature is for.
HOST_ENV = "CLAUDECODE"

# What `COLORTERM`/`TERM` have to say for the 256-colour rung to be used. Narrow
# on purpose: the fallback is 16-colour, which renders everywhere colour renders
# at all, so being wrong here costs an amber that becomes a bold yellow.
_TRUECOLOR_VALUES = frozenset({"truecolor", "24bit"})
_ANSI256_TERM_MARKERS = ("256color", "direct")


def color_capability(
    preference: ColorPreference,
    environ: "dict[str, str] | None" = None,
    stream: object = None,
) -> render.ColorCapability:
    """How much colour may actually be emitted, given preference and environment.

    The order is: the two refusals that are not preferences, then the explicit
    preference, then detection. `NO_COLOR` and `TERM=dumb` come first because one
    is an accessibility opt-out and the other is a statement of incapability, and
    neither is something a registry written months earlier should be able to
    overrule.

    Detection treats a real TTY and a Claude Code capture as equally colourable,
    and everything else as not. A pipe into a file, a `$(...)`, a test harness
    reading our stdout -- those are destinations where an escape sequence becomes
    literal text in whatever reads them next, which is how a colour feature turns
    into corrupted evidence in a bug report.
    """
    env = os.environ if environ is None else environ
    if env.get(NO_COLOR_ENV):
        return render.ColorCapability.NONE
    if (env.get(TERM_ENV) or "").strip().lower() == "dumb":
        return render.ColorCapability.NONE
    if preference is ColorPreference.NEVER:
        return render.ColorCapability.NONE
    if preference is ColorPreference.ANSI16:
        return render.ColorCapability.ANSI16
    if preference is ColorPreference.ANSI256:
        return render.ColorCapability.ANSI256
    if not (_is_a_terminal(stream) or env.get(HOST_ENV)):
        return render.ColorCapability.NONE
    return _richest_rung(env)


def _is_a_terminal(stream: object) -> bool:
    """Whether `stream` is a terminal, treating an unanswerable question as no.

    A stream object that cannot be asked -- a `StringIO` in a test, a wrapper
    without the method -- is not evidence of a terminal, and guessing yes would
    put escape sequences into the one place they are hardest to notice.
    """
    try:
        return bool(stream is not None and stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def _richest_rung(env: "dict[str, str]") -> render.ColorCapability:
    """The best colour rung the environment claims, defaulting to the safe one."""
    if (env.get(COLORTERM_ENV) or "").strip().lower() in _TRUECOLOR_VALUES:
        return render.ColorCapability.ANSI256
    term = (env.get(TERM_ENV) or "").strip().lower()
    if any(marker in term for marker in _ANSI256_TERM_MARKERS):
        return render.ColorCapability.ANSI256
    return render.ColorCapability.ANSI16


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
    depth: render.InformationDepth
    width_budget: int | None
    deadline_ms: int
    # The *preference*, not the capability: the capability depends on the
    # environment and the output stream, which are known at render time and not
    # when this document was written. Defaulted so a registry predating the field
    # reads as `auto` rather than failing to parse.
    color: ColorPreference = ColorPreference.AUTO


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
    # The id is validated before the `enabled` check, unlike everything else in
    # the entry, because the two mean different things about the document. A
    # disabled entry's command will never run, so not checking it costs nothing;
    # an id our own lifecycle could not have written means the registry was
    # produced by something else, and that is a reason to distrust the whole
    # document rather than one row of it.
    try:
        provider = contract.require_provider_id(payload.get("provider"))
    except contract.ContractViolation as exc:
        # Translated rather than propagated: `main` handles a bad registry by
        # preserving the user's line, and it recognises this failure by type. A
        # contract exception escaping from here would reach the host as a
        # traceback and take their statusline with it.
        raise RegistryError(f"provider id is not valid: {exc}") from exc
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


def _parse_upstream_command(upstream: object) -> str | None:
    """The user's original command string, or `None` if there was not one.

    Absent and present-but-null are both ordinary: a user who had no statusline
    before enabling a provider is a supported state, not a degraded one. A
    present-but-blank command is refused instead, because that is our own writer
    having recorded something it could not have meant.
    """
    if upstream is None:
        return None
    if not isinstance(upstream, dict):
        raise RegistryError("upstream must be an object or absent")
    command = upstream.get("command")
    if command is not None and (not isinstance(command, str) or not command.strip()):
        raise RegistryError("upstream.command must be a non-empty string or absent")
    return command


def _parse_presentation(presentation: object) -> tuple[object, object, int | None, object]:
    """The raw mode, depth and colour preferences, and the validated width budget.

    Returns mode, depth and colour unvalidated on purpose — `parse_registry` hands
    them to parsers that fall back rather than refuse, for the reason given there.
    The width is validated here because a nonsensical budget is not a cosmetic
    problem: it decides how much gets dropped from the line.
    """
    if not isinstance(presentation, dict):
        raise RegistryError("presentation must be an object")
    width = presentation.get("width_budget")
    if width is not None and (isinstance(width, bool) or not isinstance(width, int) or width < 1):
        raise RegistryError("presentation.width_budget must be a positive integer or absent")
    return (
        presentation.get("mode"),
        presentation.get("depth"),
        width,
        presentation.get("color"),
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
    # The type is checked as well as the value because membership alone is not a
    # version check in Python: `True` and `1.0` both compare equal to `1`, so a
    # document whose version field is a boolean would otherwise be accepted as
    # version 1 and read with a schema nobody claimed it follows.
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version not in SUPPORTED_REGISTRY_VERSIONS
    ):
        raise RegistryError(
            f"registry_version {version!r} is not supported by this compositor "
            f"(supported: {sorted(SUPPORTED_REGISTRY_VERSIONS)})"
        )

    upstream_command = _parse_upstream_command(payload.get("upstream"))

    entries = payload.get("providers", [])
    if not isinstance(entries, list):
        raise RegistryError("providers must be a list")
    if len(entries) > MAX_PROVIDERS:
        raise RegistryError(f"at most {MAX_PROVIDERS} providers may be registered")
    providers = tuple(entry for entry in map(_parse_provider, entries) if entry is not None)
    identifiers = [entry.provider for entry in providers]
    if len(identifiers) != len(set(identifiers)):
        raise RegistryError("provider ids must be unique in the registry")

    mode, depth, width, color = _parse_presentation(payload.get("presentation", {}))

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
        mode=render.PresentationMode.parse(mode),
        # Same fallback reasoning, with one difference that matters: an
        # unrecognised depth resolves to the *narrower* of the two, so a typo can
        # never answer with more information than the reader asked for.
        depth=render.InformationDepth.parse(depth),
        width_budget=width,
        deadline_ms=_bounded_ms(payload, "deadline_ms", DEFAULT_DEADLINE_MS, MAX_DEADLINE_MS),
        # Falls back to *off* rather than to the default, unlike the two above.
        # See `ColorPreference.parse` for why the asymmetry is the safe direction.
        color=ColorPreference.parse(color),
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


def depth() -> int:
    """How many compositors are already in this process' ancestry."""
    try:
        return max(0, int(os.environ.get(DEPTH_ENV, "0")))
    except ValueError:
        # An unparseable marker is treated as "someone is already here", because
        # the failure we are preventing is unbounded recursion.
        return MAX_DEPTH


def child_env() -> dict:
    """The environment every child gets, carrying the recursion marker."""
    env = dict(os.environ)
    env[DEPTH_ENV] = str(depth() + 1)
    return env


def _run_bounded(
    command: str | tuple[str, ...], payload: bytes, timeout_ms: int, *, shell: bool
) -> tuple[int | None, bytes]:
    """Run a child under a hard time bound and return `(returncode, stdout)`.

    Deliberately not `subprocess.run`, for one reason: on timeout it kills only
    the direct child. A provider implemented as a shell script that forks a
    worker leaves that worker orphaned, and this command runs on every
    statusline refresh — a few times a minute, for as long as the session lasts
    — so a per-refresh leak accumulates into a real process count on the user's
    machine. Each child therefore gets its own session and the timeout kills the
    whole process group.

    The wait after the kill is itself bounded. An orphan holding the write end of
    our stdout pipe can delay EOF for as long as it likes, and blocking there
    would hand a misbehaving provider the ability to stall the host's render.

    `returncode` is `None` when the child was killed for exceeding its bound, so
    a timeout is distinguishable from a clean non-zero exit. Partial stdout is
    returned either way: output already produced is still output.
    """
    child = subprocess.Popen(
        command,
        # Only ever true for the user's own configured command; see run_upstream
        # for why passing that string to a shell unmodified is the safe option.
        shell=shell,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=child_env(),
        start_new_session=True,
    )
    try:
        produced, _ = child.communicate(input=payload, timeout=timeout_ms / 1000)
        return child.returncode, produced or b""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        except OSError:
            child.kill()
        try:
            produced, _ = child.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            produced = b""
        return None, produced or b""


def names_this_command(command: str) -> bool:
    """Whether `command` looks like it re-invokes this very script.

    A second, independent guard beside `DEPTH_ENV`, for the case where the shell
    drops the environment. It compares the first whitespace-separated token to
    this file by inode, which is an inspection, not a rewrite — the command
    string handed to the shell is still the exact one from the registry.

    Conservative by construction: it can only produce a false *negative* (a
    quoted or `exec`-wrapped self-reference it fails to spot), and that case is
    still caught by the depth marker one level down.
    """
    token = command.strip().split()[0] if command.strip() else ""
    if not token:
        return False
    candidate = shutil.which(token) or token
    try:
        return os.path.samefile(candidate, __file__)
    except OSError:
        return False


def run_upstream(command: str, payload: bytes, timeout_ms: int) -> str:
    """Run the user's original statusline command and return its stdout.

    Executed through the shell with the command string exactly as configured.
    That is the point: the host runs this slot through a shell, so handing the
    same string to a shell reproduces its quoting, spaces and arguments without
    this module ever having to understand them. Splitting it into an argv would
    mean parsing the user's command, which is the one thing we must not do.

    `payload` is forwarded byte for byte — the same stdin the host gave us.

    Never raises. Every failure path returns whatever output was produced, or
    the empty string, because losing the user's line is the worst outcome
    available here and a diagnostic they cannot see would not make up for it.
    """
    try:
        # The return code is deliberately ignored. A statusline script that
        # prints its line and then exits non-zero still printed their line, and
        # deciding their output is invalid on their behalf is not ours to do.
        _, produced = _run_bounded(command, payload, timeout_ms, shell=True)
    except OSError:
        produced = b""
    text = produced[:MAX_UPSTREAM_OUTPUT_BYTES].decode("utf-8", errors="replace")
    # Only the trailing newline goes: leading and internal whitespace is theirs,
    # including the indentation a padded statusline relies on.
    return text.rstrip("\n")


def _host_not_available(
    entry: ProviderEntry, availability: contract.Availability, reason_code: str, reason_label: str
) -> contract.ProviderStatus:
    """The status the *host* writes on a provider's behalf when it did not answer.

    `provider_version` is the literal `unknown`, because a provider that failed
    to answer did not tell us its version and inventing one would be a claim
    about which build is installed.
    """
    return contract.not_available(
        provider=entry.provider,
        provider_version="unknown",
        scope=entry.scope,
        availability=availability,
        reason_code=reason_code,
        reason_label=reason_label,
    )


#: Version of the identity-context document the compositor builds and sends
#: to providers as their stdin (HORO-1602). Independent of `cache_version`
#: and of the provider-contract's own `contract_version` -- this is a third,
#: narrower surface: what the host tells a provider about the invocation,
#: not what a provider tells the host about its status.
_IDENTITY_STDIN_VERSION = 1


def extract_provider_session_id(host_payload: bytes) -> str | None:
    """Best-effort, narrow extraction of Claude Code's own `session_id` from
    the host's raw stdin bytes -- the ONE field this function is permitted to
    read out of that payload (HORO-1602).

    This is the single, deliberate, allowlisted exception to `run_provider`'s
    own invariant that a provider never receives the host's payload: the host
    may read exactly this one field from it, for the sole purpose of building
    a minimal identity document (see `build_identity_stdin`) -- never prompts,
    paths, model identifiers, tool content, or any other key the payload may
    carry. Every other key in `host_payload` remains exactly as opaque to
    this module as it was before this function existed.

    Never raises. A malformed, non-JSON, non-object, or missing-field payload
    all return `None` -- the same "not known" outcome as a working Claude Code
    session that simply did not include the field in some payload shape this
    module has not been told about. `None` here must never be upgraded into
    a fabricated placeholder downstream; see `build_identity_stdin`.
    """
    try:
        document = json.loads(host_payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    session_id = document.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def build_identity_stdin(provider_session_id: str | None) -> bytes:
    """The minimal, allowlisted document a provider receives as its own
    stdin (HORO-1602) -- never the host's raw payload, never more than this
    one field.

    Returns `b""`, byte-identical to every provider invocation before this
    feature existed, when `provider_session_id` is `None` -- a provider that
    reads nothing from stdin, or that checks for this field and finds it
    absent, observes exactly the same behavior as before this feature
    shipped. This is what keeps the change additive rather than a breaking
    bump: no existing provider's behavior changes unless it opts in to
    reading the new field.
    """
    if provider_session_id is None:
        return b""
    return json.dumps(
        {
            "identity_stdin_version": _IDENTITY_STDIN_VERSION,
            "provider_session_id": provider_session_id,
        }
    ).encode("utf-8")


def source_fingerprint(argv: tuple[str, ...]) -> str:
    """Identify *which command* produced a cached answer.

    The cache is keyed by provider id, because that is what the registry
    guarantees is unique. But the id alone does not say which executable
    answered, so an entry written before the registry was re-pointed would
    otherwise be served as though the new command had said it. Recording a
    digest of the argv lets a re-pointed provider ignore the old answer instead
    of attributing it. The argv is not secret — it is hashed only to keep local
    paths out of a file the user may reasonably inspect.
    """
    joined = "\x00".join(argv).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def read_cache(
    entry: ProviderEntry, home: pathlib.Path | None = None, *, identity: str | None = None
) -> contract.ProviderStatus | None:
    """A provider's last answer, if it is still inside its own stated TTL.

    Only ever returns an *unexpired* entry. A stale cache is not served as a
    substitute for a failed probe: the two mean different things, and rendering
    last minute's healthy reading while the daemon is down is precisely the lie
    the contract's not-available states exist to prevent.

    `identity` (HORO-1602) is the current render's `provider_session_id`, the
    same value `collect` is about to pass as this provider's stdin. An entry
    written under a different identity -- including one written when no
    identity was known at all -- is treated as a miss, never served: a cache
    keyed only on provider id would let a provider that starts emitting real
    per-session data in a future render hand session A's cached answer to
    session B's render a moment later. Most providers ignore the identity
    stdin entirely and answer identically regardless, so this costs them one
    extra probe at most once per session change, never a wrong answer.

    Known residual gap (adversarial review, HORO-1602): two different,
    concurrent renders that *both* fail to resolve any session id (`identity`
    is `None` on both sides -- an older host payload shape, or statusline run
    outside any coding-agent session at all) still match each other, because
    `None == None`. This is not a regression: pre-HORO-1602 caching had no
    identity concept at all, so this is the same behavior the cache always
    had for that case, not a new leak. Closing it fully would mean an unknown
    identity always bypasses the cache -- rejected here because it would
    defeat caching entirely for every install that has no session id to give
    (the common case for a bare terminal statusline), a real regression for a
    theoretical, same-host-only collision that only matters for a provider
    that (a) actually emits per-session data and (b) is queried by two
    genuinely different unidentified sessions inside the same ~60s TTL
    window -- not true of any provider registered today.
    """
    path = cache_dir(home) / f"{entry.provider}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("expires_at"), (int, float)):
        return None
    if payload["expires_at"] <= time.time():
        return None
    if payload.get("source") != source_fingerprint(entry.argv):
        return None
    if payload.get("identity") != identity:
        return None
    try:
        return contract.provider_status_from_wire(payload.get("wire"))
    except contract.ContractViolation:
        return None


def write_cache(
    entry: ProviderEntry,
    status: contract.ProviderStatus,
    home: pathlib.Path | None = None,
    *,
    identity: str | None = None,
) -> None:
    """Persist a fresh answer for up to its own TTL. Best effort, never fatal.

    Written to a temporary file in the same directory and renamed, so a refresh
    that is interrupted mid-write leaves the previous entry intact rather than a
    half-written one. The cache is Horonom-owned state, so the directory is
    0700 and the file 0600: it is not secret by contract, but it is not the
    user's to have to reason about either.

    `identity` (HORO-1602) is recorded alongside the answer so a later
    `read_cache` call can tell whether this entry was produced under the
    same provider-session identity as the render asking for it -- see
    `read_cache`'s own docstring.
    """
    ttl = min(status.cache_ttl_seconds, contract.MAX_CACHE_TTL_SECONDS)
    if ttl <= 0:
        return
    directory = cache_dir(home)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "cache_version": 1,
            "expires_at": time.time() + ttl,
            "source": source_fingerprint(entry.argv),
            "identity": identity,
            "wire": status.to_wire(),
        }
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=f".{status.provider}-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
            os.chmod(temporary, 0o600)
            os.replace(temporary, directory / f"{status.provider}.json")
        except BaseException:
            os.unlink(temporary)
            raise
    except (OSError, TypeError, ValueError):
        # A cache is an optimisation. Failing to write one must not change what
        # the user sees this refresh or any other.
        return


def run_provider(
    entry: ProviderEntry,
    timeout_ms: int,
    home: pathlib.Path | None = None,
    *,
    provider_session_id: str | None = None,
) -> contract.ProviderStatus:
    """Get one provider's status, from cache if fresh, else by asking it.

    Never raises and never returns `None`: every failure becomes an explicit
    not-available status, so a provider that broke is visibly a provider that
    broke rather than a gap in the line.

    Reasons are fixed codes and short fixed prose, never the exception text. A
    subprocess error message routinely contains a path, a command line or an
    environment value, and this string is rendered into the user's terminal.

    `provider_session_id` (HORO-1602) is forwarded to both the cache lookup
    (`read_cache`/`write_cache`'s `identity`) and the provider's own stdin
    (`build_identity_stdin`) -- the same value, so a cache entry answered
    under one identity is never served under another.
    """
    cached = read_cache(entry, home, identity=provider_session_id)
    if cached is not None:
        return cached
    if timeout_ms <= 0:
        return _host_not_available(
            entry, contract.Availability.UNKNOWN, "deadline_exhausted", "No time left to ask"
        )
    try:
        # Never the host's raw payload -- providers remain denied that, same
        # as before HORO-1602: it is the one thing here that could carry
        # prompts, paths, model identifiers or tool content, and a provider
        # that cannot receive it cannot render it, log it, or grow a
        # dependency on it. `build_identity_stdin` is the one narrow,
        # allowlisted exception -- a provider_session_id and nothing else,
        # `b""` when none is known, byte-identical to pre-HORO-1602
        # behavior in that case. Only the user's own command, which the
        # host was already feeding before we took the slot, gets the real
        # payload.
        returncode, produced = _run_bounded(
            entry.argv, build_identity_stdin(provider_session_id), timeout_ms, shell=False
        )
    except FileNotFoundError:
        return _host_not_available(
            entry, contract.Availability.UNSUPPORTED, "not_installed", "Not installed"
        )
    except OSError:
        return _host_not_available(
            entry, contract.Availability.ERROR, "probe_failed", "Could not be started"
        )
    if returncode is None:
        return _host_not_available(
            entry, contract.Availability.ERROR, "probe_timeout", "Did not answer in time"
        )
    if returncode != 0:
        return _host_not_available(
            entry, contract.Availability.ERROR, "probe_failed", "Reported an error"
        )
    try:
        payload = json.loads(produced[:MAX_PROVIDER_OUTPUT_BYTES].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _host_not_available(
            entry, contract.Availability.ERROR, "malformed_output", "Sent unreadable output"
        )
    try:
        status = contract.provider_status_from_wire(payload)
    except contract.ContractViolation:
        return _host_not_available(
            entry, contract.Availability.ERROR, "contract_violation", "Sent an invalid status"
        )
    if status.provider != entry.provider:
        # A provider answering under another product's id would let it attribute
        # its own state to that product, or hide behind it.
        return _host_not_available(
            entry, contract.Availability.ERROR, "identity_mismatch", "Answered as another provider"
        )
    write_cache(entry, status, home, identity=provider_session_id)
    return status


def collect(
    registry: Registry, payload: bytes, home: pathlib.Path | None = None
) -> tuple[str, tuple[contract.ProviderStatus, ...]]:
    """Run the upstream command and every provider, concurrently.

    One pool for all of them, so the wall clock is about the slowest single
    child rather than the sum of the timeouts — serialising independent
    subprocesses would make the worst case grow with every product the user
    enables, which is exactly the cost this whole design exists to avoid.

    Each provider gets the smaller of its own timeout and the time left on the
    overall deadline, so the deadline is a real bound and not an aspiration. The
    upstream command has its own, more generous budget and is not subject to the
    provider deadline: preserving the user's line outranks our own promptness.

    `payload` is read exactly once here, via `extract_provider_session_id`
    (HORO-1602), for the sole purpose of building the identity stdin every
    provider submission below receives -- see that function's docstring for
    the narrow, allowlisted scope of what it is permitted to read out of it.
    """
    started = time.monotonic()
    provider_session_id = extract_provider_session_id(payload)
    workers = max(1, len(registry.providers) + (1 if registry.upstream_command else 0))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        upstream_future = (
            pool.submit(run_upstream, registry.upstream_command, payload, registry.upstream_timeout_ms)
            if registry.upstream_command
            else None
        )
        provider_futures = [
            (
                entry,
                pool.submit(
                    run_provider,
                    entry,
                    min(
                        entry.timeout_ms,
                        # Recomputed per submission rather than once, so a slow
                        # scheduler eats into the budget it actually delayed.
                        max(0, registry.deadline_ms - int((time.monotonic() - started) * 1000)),
                    ),
                    home,
                    provider_session_id=provider_session_id,
                ),
            )
            for entry in registry.providers
        ]

        statuses = []
        for entry, future in provider_futures:
            remaining = registry.deadline_ms - int((time.monotonic() - started) * 1000)
            try:
                statuses.append(future.result(timeout=max(0.0, remaining / 1000)))
            except concurrent.futures.TimeoutError:
                # The child's own timeout should have fired first; this is the
                # backstop for a provider that hangs outside the subprocess call.
                statuses.append(
                    _host_not_available(
                        entry,
                        contract.Availability.ERROR,
                        "deadline_exceeded",
                        "Missed the render deadline",
                    )
                )
        # No thread-level deadline on the upstream, unlike the providers above,
        # and not an oversight: `run_upstream` cannot block past its subprocess
        # timeout, and a timeout here would be theatre anyway. The pool joins its
        # workers on `with` exit, and `concurrent.futures` joins them again at
        # interpreter exit, so a thread that genuinely hung could not be escaped
        # by declining to wait for it here. The bound that does the work is the
        # one inside `_run_bounded`.
        upstream_text = upstream_future.result() if upstream_future else ""
    return upstream_text, tuple(statuses)


def _render(reader: object, writer: object) -> None:
    """Do the work of one render, writing one statusline to `writer`.

    One statusline, not one line: at detail depth the renderer lays each product
    out on a row of its own, and the upstream statusline may have been several
    rows before we ever saw it. The composed text is printed as it comes back.

    Split from `main` so that "this command always exits 0" is structural rather
    than repeated: every early exit here is a bare `return`, and there is exactly
    one place in the module that decides the exit status. Written as one function
    with a `return 0` at each of several exits, the invariant would hold only for
    as long as everyone adding an exit remembered it.
    """
    payload = reader.read() or b""

    if depth() >= MAX_DEPTH:
        # Already inside a compositor. Printing nothing is correct: whatever
        # invoked us is going to print the line, and printing a second copy of
        # everything is how a recursion becomes visible instead of just deep.
        print("horonom-statusline: refusing to recurse", file=sys.stderr)
        return

    try:
        registry = load_registry()
    except RegistryError as exc:
        # Our own state is broken and we cannot learn what their original
        # command was, so there is nothing to preserve. Say so on the line
        # rather than printing an empty statusline: a blank line reads as "no
        # status", which is indistinguishable from working correctly.
        print(f"horonom-statusline: {exc}", file=sys.stderr)
        print(
            f"{render.state_marker('unknown', render.PresentationMode.PLAIN)} "
            "Horonom statusline registry unreadable",
            file=writer,
        )
        return

    if registry.upstream_command and names_this_command(registry.upstream_command):
        print("horonom-statusline: upstream names this command; not running it", file=sys.stderr)
        registry = dataclasses.replace(registry, upstream_command=None)

    upstream_text, statuses = collect(registry, payload)
    try:
        line = render.compose(
            upstream_text,
            statuses,
            mode=registry.mode,
            width_budget=registry.width_budget,
            depth=registry.depth,
            # Resolved here, at the one point that has both the registry and the
            # stream we are about to write to. The renderer is pure and may not
            # look at either.
            color=color_capability(registry.color, stream=writer),
        )
    except Exception as exc:  # noqa: BLE001
        # The broadest catch in this module, and deliberately so: the first
        # invariant is that no failure of ours suppresses the user's line, and a
        # defect in our own rendering is still a failure of ours. They lose our
        # block, which is ours to lose, and keep their line, which is not.
        print(f"horonom-statusline: render failed ({type(exc).__name__})", file=sys.stderr)
        line = upstream_text
    print(line, file=writer)


def main(argv: list[str] | None = None, stdin: object = None, stdout: object = None) -> int:
    """Render the statusline. Exits 0 in every case a user can cause.

    A statusline command that exits non-zero gives the host nothing useful to do
    and risks turning a bad registry into log noise on every refresh, so problems
    go to stderr, where the diagnostic surface can find them, and the line still
    renders. This is the only place the exit status is decided.
    """
    del argv  # The host passes no arguments; accepted for testability only.
    _render(
        stdin if stdin is not None else sys.stdin.buffer,
        stdout if stdout is not None else sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
