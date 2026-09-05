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

Stdlib only, deterministic, idempotent (`--check` exits non-zero on
drift), fail-closed on a malformed canonical skill — matches
scripts/generate_company_metadata.py's contract.

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


def build_projections() -> dict[Path, str]:
    projections: dict[Path, str] = {}
    for name in discover_skills():
        canonical = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        projections[CLAUDE_SKILLS_DIR / name / "SKILL.md"] = render_claude_projection(name, canonical)
        projections[CODEX_SKILLS_DIR / f"{name}.md"] = render_codex_projection(name, canonical)
    return projections


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit non-zero on drift; write nothing.")
    args = parser.parse_args(argv)

    try:
        projections = build_projections()
    except SkillProjectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for path in projections:
        # Every projection must land under .claude/skills/ or .codex/skills/
        # — never anywhere else, even if a future skill name were crafted
        # to try to escape (discover_skills already rejects unsafe names,
        # this is the belt-and-suspenders check at the write boundary).
        if CLAUDE_SKILLS_DIR not in path.parents and CODEX_SKILLS_DIR not in path.parents:
            raise SkillProjectionError(f"refusing to write outside adapter dirs: {path}")

    drifted = [p for p, content in projections.items() if _read_or_empty(p) != content]
    if not drifted:
        print("Skill projections are up to date.")
        return 0
    if args.check:
        for p in drifted:
            print(f"DRIFT: {p.relative_to(REPO_ROOT)} does not match its canonical SKILL.md.", file=sys.stderr)
        print("Run: python3 agents/common/project_skills.py", file=sys.stderr)
        return 1
    for p in drifted:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(projections[p], encoding="utf-8")
        print(f"Wrote {p.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
