#!/usr/bin/env python3
"""Project canonical Horonom skills into tool-specific adapter locations.

`agents/skills/<name>/SKILL.md` is the single canonical source for each
shared company skill (HORO-509). Neither the Claude adapter nor the Codex
adapter hand-copies a skill's content (ADR-0005 decisions #5/#6) — this
script generates both projections from the same source:

  * `.claude/skills/<name>/SKILL.md` — verbatim canonical content plus a
    generated provenance header. Consumed by Claude Code.
  * `.codex/skills/<name>.md` — a flattened equivalent for Codex. This
    shape is provisional pending HORO-508's real Codex adapter design;
    it exists so Codex has *something* real to read today rather than
    nothing, without pretending to be the final design.

A skill may also carry the optional progressive-disclosure package assets
defined by HORO-969's `governance/engineering/agent-skill-architecture.md`
§2 — `manifest.yaml`, `references/`, `examples/`, `scripts/`, `tests/`.
None of the five pre-HORO-970 skills use these; they remain fully valid
with `SKILL.md` alone (§9 of that doc — this is additive, not a migration).
Asset projection (`build_asset_projections()`) currently targets this
repo's own `.claude/skills/<name>/` tree only; cross-repo asset
distribution into an adopted/consumer repo is HORO-982's scope, not
reimplemented here — `scripts/repo_bootstrap.py` still projects `SKILL.md`
only via `build_projections()`, unchanged.

Stdlib only, deterministic, idempotent (`--check` exits non-zero on
drift), fail-closed on a malformed canonical skill or manifest — matches
scripts/generate_company_metadata.py's contract.

`build_projections()` targets this repo's own `.claude/`/`.codex/` by
default. HORO-507/HORO-511's cross-repo adoption path
(`scripts/repo_bootstrap.py adopt`) calls it with `dest_root` set to a
*different* target repo, so an adopted product repo gets the same
canonical skill content projected into its own `.claude/skills/`/
`.codex/skills/` — never a second hand-copied implementation.

Usage:
    python3 agents/common/project_skills.py
    python3 agents/common/project_skills.py --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "agents" / "skills"
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
CODEX_SKILLS_DIR = REPO_ROOT / ".codex" / "skills"

GENERATED_MARKER = "<!-- horonom:generated -->"
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# The only asset directories the projection/applicability model recognizes
# (HORO-969 §2). Anything else under a skill directory is not projected and
# not an error by itself — discover_skills() only requires SKILL.md — but
# an unrecognized *file* directly beside SKILL.md (not one of these dirs,
# not manifest.yaml) is almost certainly a mistake, so asset discovery
# below only ever descends into these four names.
_ASSET_DIRS = ("references", "examples", "scripts", "tests")
_MANIFEST_NAME = "manifest.yaml"


class SkillProjectionError(RuntimeError):
    pass


def discover_skills() -> list[str]:
    if not SKILLS_DIR.is_dir():
        raise SkillProjectionError(f"no such directory: {SKILLS_DIR}")
    names = []
    for entry in sorted(SKILLS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if not _SAFE_NAME_RE.match(entry.name):
            raise SkillProjectionError(f"unsafe skill directory name: {entry.name!r}")
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            raise SkillProjectionError(f"agents/skills/{entry.name}/SKILL.md is missing")
        names.append(entry.name)
    if not names:
        raise SkillProjectionError(f"no skills found under {SKILLS_DIR}")
    return names


def discover_skill_assets(name: str) -> list[Path]:
    """Return every asset file under agents/skills/<name>/{references,
    examples,scripts,tests}/, as paths relative to the skill directory.

    Fails closed (raises SkillProjectionError) on:
      * a symlink anywhere in an asset path (file or intermediate dir) —
        a symlink could point outside the canonical skill directory and
        get silently followed into someone else's files;
      * a resolved path that is not actually inside the skill directory
        (belt-and-suspenders against the symlink check, and against a
        pathological name components can't normally produce).

    Does not require any of the four asset dirs to exist — all optional.
    """
    skill_dir = (SKILLS_DIR / name).resolve()
    assets: list[Path] = []
    for asset_dir_name in _ASSET_DIRS:
        asset_dir = SKILLS_DIR / name / asset_dir_name
        if not asset_dir.is_dir():
            continue
        if asset_dir.is_symlink():
            raise SkillProjectionError(f"{name}/{asset_dir_name} is a symlink — refusing to follow it")
        for path in sorted(asset_dir.rglob("*")):
            if path.is_dir():
                continue
            if any(part.is_symlink() for part in _ancestors_within(path, asset_dir)):
                raise SkillProjectionError(f"{name}: asset path contains a symlink: {path}")
            resolved = path.resolve()
            if skill_dir not in resolved.parents:
                raise SkillProjectionError(f"{name}: asset path escapes its skill directory: {path}")
            assets.append(path.relative_to(SKILLS_DIR / name))
    return assets


def _ancestors_within(path: Path, stop_at: Path) -> list[Path]:
    """path and every ancestor of it up to (inclusive of) stop_at."""
    out = [path]
    current = path
    while current != stop_at and current.parent != current:
        current = current.parent
        out.append(current)
    return out


# ---------------------------------------------------------------------------
# manifest.yaml — a deliberately restricted subset, not general YAML.
#
# Stdlib only (no PyYAML dependency), matching this module's existing
# contract. Supported shape, exactly:
#
#   applicability:
#     stacks: [rust, python]
#     requires_all: false
#
# `stacks` lists the repo-evidence stack tags from HORO-969's applicability
# table (rust/python/typescript/go/swift/terraform/container). `requires_all`
# defaults to false (skill applies if the repo matches ANY listed stack);
# true means the repo must match ALL listed stacks (e.g. a PyO3-specific
# skill wanting python AND rust both present).
#
# Anything outside this exact shape — unknown top-level key, `applicability`
# not a mapping, `stacks` not a flow list of bare words, `requires_all` not
# `true`/`false` — is a malformed manifest and fails closed.
# ---------------------------------------------------------------------------

_KNOWN_STACKS = frozenset({"rust", "python", "typescript", "go", "swift", "terraform", "container"})


def load_manifest(name: str) -> dict | None:
    """Returns None if the skill has no manifest.yaml (fully valid — most
    skills don't need one). Raises SkillProjectionError on anything
    present but malformed; never returns a partially-parsed result."""
    manifest_path = SKILLS_DIR / name / _MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    if manifest_path.is_symlink():
        raise SkillProjectionError(f"{name}/{_MANIFEST_NAME} is a symlink — refusing to follow it")
    text = manifest_path.read_text(encoding="utf-8")
    seen_keys: set[str] = set()
    stacks: list[str] | None = None
    requires_all = False
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines or lines[0].rstrip() != "applicability:":
        raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: expected top-level 'applicability:' key")
    for line in lines[1:]:
        if not line.startswith("  "):
            raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: unexpected top-level content: {line!r}")
        stripped = line.strip()
        if stripped.startswith("stacks:"):
            if "stacks" in seen_keys:
                raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: duplicate key 'stacks'")
            seen_keys.add("stacks")
            raw = stripped[len("stacks:") :].strip()
            if not (raw.startswith("[") and raw.endswith("]")):
                raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: 'stacks' must be a flow list like [rust, python]")
            items = [item.strip() for item in raw[1:-1].split(",") if item.strip()]
            for item in items:
                if item not in _KNOWN_STACKS:
                    raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: unknown stack {item!r}")
            stacks = items
        elif stripped.startswith("requires_all:"):
            if "requires_all" in seen_keys:
                raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: duplicate key 'requires_all'")
            seen_keys.add("requires_all")
            raw = stripped[len("requires_all:") :].strip()
            if raw not in ("true", "false"):
                raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: 'requires_all' must be true or false")
            requires_all = raw == "true"
        else:
            raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: unrecognized key: {stripped!r}")
    if not stacks:
        raise SkillProjectionError(f"{name}/{_MANIFEST_NAME}: 'applicability.stacks' must list at least one stack")
    return {"stacks": stacks, "requires_all": requires_all}


# ---------------------------------------------------------------------------
# Applicability resolution — HORO-969 §3's evidence-based detection order,
# for stack-tagged skills (skills with no manifest.yaml are always
# applicable — this is what keeps the five pre-HORO-970 skills unaffected).
# ---------------------------------------------------------------------------

_STACK_EVIDENCE: dict[str, tuple[str, ...]] = {
    "rust": ("Cargo.toml",),
    "python": ("pyproject.toml",),
    "typescript": ("package.json", "tsconfig.json"),
    "go": ("go.mod",),
    "swift": ("Package.swift",),
    "terraform": (),  # glob handled specially, see below
    "container": ("Dockerfile", "docker-compose.yml"),
}


def _repo_has_stack_evidence(repo_root: Path, stack: str) -> bool:
    if stack == "terraform":
        return any(repo_root.glob("*.tf")) or any(repo_root.glob("**/*.tf"))
    for filename in _STACK_EVIDENCE.get(stack, ()):
        if (repo_root / filename).exists():
            return True
    return False


def resolve_applicable_skills(repo_root: Path, *, overrides: dict[str, bool] | None = None) -> list[str]:
    """Which canonical skills apply to `repo_root`, evidence-based.

    A skill with no manifest.yaml is always applicable (backward
    compatible with the five pre-HORO-970 skills — "every repo receives
    every skill" is forbidden by HORO-969 §3 only for *stack-tagged*
    skills, not for company-process skills like jira-delivery that have
    no stack dependency at all).

    `overrides`, keyed by skill name, forces a skill in (`True`) or out
    (`False`) regardless of evidence — HORO-969 §3's "explicit override
    always wins" rule, checked first. Raises SkillProjectionError if an
    override names a skill that doesn't exist — a misspelled override
    must fail loudly, not silently no-op.
    """
    overrides = overrides or {}
    names = discover_skills()
    unknown = set(overrides) - set(names)
    if unknown:
        raise SkillProjectionError(f"override(s) name unknown skill(s): {sorted(unknown)}")
    applicable: list[str] = []
    for name in names:
        if name in overrides:
            if overrides[name]:
                applicable.append(name)
            continue
        manifest = load_manifest(name)
        if manifest is None:
            applicable.append(name)
            continue
        matches = [_repo_has_stack_evidence(repo_root, stack) for stack in manifest["stacks"]]
        is_applicable = all(matches) if manifest["requires_all"] else any(matches)
        if is_applicable:
            applicable.append(name)
    return applicable


def render_claude_projection(name: str, canonical: str) -> str:
    return (
        f"{GENERATED_MARKER}\n"
        f"<!-- Source: horonomy/.github agents/skills/{name}/SKILL.md. "
        f"Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->\n\n"
        f"{canonical}"
    )


def render_codex_projection(name: str, canonical: str) -> str:
    # Flattened, single-file form for Codex (provisional — see
    # agents/common/README.md). Strips nothing from the canonical content;
    # only adds the provenance header, matching the Claude projection's
    # "never fork a second hand-maintained copy" rule.
    return (
        f"{GENERATED_MARKER}\n"
        f"<!-- Source: horonomy/.github agents/skills/{name}/SKILL.md. "
        f"Provisional Codex projection shape — see agents/common/README.md. "
        f"Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->\n\n"
        f"{canonical}"
    )


def build_projections(dest_root: Path | None = None) -> dict[Path, str]:
    """`dest_root` defaults to this repo (self-projection). Passing a
    different repo's path projects the *same* canonical content there —
    used by scripts/repo_bootstrap.py's cross-repo adoption path.

    SKILL.md only — see build_asset_projections() for the separate
    references/examples/scripts/tests asset tree, which today is
    self-projection only (see module docstring)."""
    claude_dir = (dest_root / ".claude" / "skills") if dest_root else CLAUDE_SKILLS_DIR
    codex_dir = (dest_root / ".codex" / "skills") if dest_root else CODEX_SKILLS_DIR
    projections: dict[Path, str] = {}
    for name in discover_skills():
        canonical = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        projections[claude_dir / name / "SKILL.md"] = render_claude_projection(name, canonical)
        projections[codex_dir / f"{name}.md"] = render_codex_projection(name, canonical)
    return projections


def build_asset_projections() -> dict[Path, bytes]:
    """Verbatim copies of every skill's references/examples/scripts/tests
    files, keyed by their destination path under this repo's own
    `.claude/skills/<name>/...`. Content is copied byte-for-byte (no
    provenance-header injection, unlike SKILL.md's projection) because an
    asset may be a script or fixture where a prepended HTML comment would
    corrupt it — the generated-ness of the whole `.claude/skills/<name>/`
    tree is the existing, already-established invariant (matches how
    SKILL.md's own self-projection already gets blindly overwritten on
    drift with no per-file collision check — see main()).

    Read as bytes, not text, so a binary fixture under tests/ round-trips
    correctly.
    """
    projections: dict[Path, bytes] = {}
    for name in discover_skills():
        for rel_path in discover_skill_assets(name):
            content = (SKILLS_DIR / name / rel_path).read_bytes()
            projections[CLAUDE_SKILLS_DIR / name / rel_path] = content
    return projections


def _read_bytes_or_none(path: Path) -> bytes | None:
    # None (not b"") for a missing file — an empty canonical asset file
    # (b"") must still be recognized as drifted-from-missing and written,
    # not treated as already matching a nonexistent destination.
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _existing_projected_asset_files(name: str) -> list[Path]:
    """Files currently on disk under `.claude/skills/<name>/{references,
    examples,scripts,tests}/` — the *destination* side, used to detect
    orphans: a projected asset whose canonical source was renamed/removed
    but whose stale generated copy would otherwise sit on disk forever,
    since drift detection alone only ever looks at current expected
    projections, never at what's actually present."""
    found: list[Path] = []
    for asset_dir_name in _ASSET_DIRS:
        d = CLAUDE_SKILLS_DIR / name / asset_dir_name
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                found.append(p)
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit non-zero on drift; write nothing.")
    args = parser.parse_args(argv)

    try:
        names = discover_skills()
        for name in names:
            load_manifest(name)  # fail closed on a malformed manifest, even if unused below
        text_projections = build_projections()
        asset_projections = build_asset_projections()
    except SkillProjectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Merge into one byte-keyed map so drift/write logic is uniform.
    projections: dict[Path, bytes] = {p: c.encode("utf-8") for p, c in text_projections.items()}
    for p, c in asset_projections.items():
        if p in projections:
            print(f"ERROR: asset projection collides with a SKILL.md projection path: {p}", file=sys.stderr)
            return 2
        projections[p] = c

    for path in projections:
        # Every projection must land under .claude/skills/ or .codex/skills/
        # — never anywhere else, even if a future skill name were crafted
        # to try to escape (discover_skills/discover_skill_assets already
        # reject unsafe names and traversal, this is the belt-and-suspenders
        # check at the write boundary).
        if CLAUDE_SKILLS_DIR not in path.parents and CODEX_SKILLS_DIR not in path.parents:
            print(f"ERROR: refusing to write outside adapter dirs: {path}", file=sys.stderr)
            return 2

    # Orphans: a projected asset file that exists on disk but no longer
    # corresponds to any current canonical asset (renamed/removed source).
    orphans: list[Path] = []
    for name in names:
        for existing in _existing_projected_asset_files(name):
            if existing not in projections:
                orphans.append(existing)

    drifted = [p for p, content in projections.items() if _read_bytes_or_none(p) != content]
    if not drifted and not orphans:
        print("Skill projections are up to date.")
        return 0
    if args.check:
        for p in drifted:
            print(f"DRIFT: {p.relative_to(REPO_ROOT)} does not match its canonical source.", file=sys.stderr)
        for p in orphans:
            print(f"DRIFT: {p.relative_to(REPO_ROOT)} is an orphaned generated asset (source renamed/removed).", file=sys.stderr)
        print("Run: python3 agents/common/project_skills.py", file=sys.stderr)
        return 1
    for p in drifted:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(projections[p])
        if p in asset_projections:
            # Preserve the canonical asset's own permission bits (e.g. a
            # scripts/ helper's executable bit) — write_bytes() always
            # creates the destination with the process's default mode,
            # which would silently strip +x otherwise.
            source = SKILLS_DIR / p.relative_to(CLAUDE_SKILLS_DIR)
            p.chmod(source.stat().st_mode & 0o777)
        print(f"Wrote {p.relative_to(REPO_ROOT)}.")
    for p in orphans:
        p.unlink()
        print(f"Removed orphaned {p.relative_to(REPO_ROOT)}.")
        # Best-effort: drop now-empty asset directories so a fully-removed
        # references/examples/scripts/tests dir doesn't linger empty.
        parent = p.parent
        while parent != CLAUDE_SKILLS_DIR and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
