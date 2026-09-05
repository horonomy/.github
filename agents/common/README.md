# agents/common — shared skill infrastructure

Non-skill-specific logic shared by every skill under `agents/skills/`:
the projection generator that turns each skill's canonical `SKILL.md` into
the tool-specific location each adapter reads from, so neither adapter
hand-copies a skill's content (ADR-0005 decisions #5 and #6).

## What lives here vs. in a skill

- **Here (`agents/common/`)**: the generator (`project_skills.py`), its
  tests, and anything else genuinely shared across all skills.
- **In a skill (`agents/skills/<name>/`)**: that skill's own `SKILL.md`
  and any skill-specific reference/script/test.

Product-specific business or security semantics never belong in either
location — they stay in the product's own repo, referenced by name from
the relevant skill (`governance/README.md`'s ownership matrix).

## Projection

```bash
python3 agents/common/project_skills.py            # write/refresh projections
python3 agents/common/project_skills.py --check    # exit non-zero on drift
```

Projects each `agents/skills/<name>/SKILL.md` to:

- `.claude/skills/<name>/SKILL.md` — verbatim content plus a generated
  provenance header, consumed by Claude Code.
- `.codex/skills/<name>.md` — a flattened equivalent for Codex. This
  format is **provisional**: HORO-508 owns the real Codex adapter design
  and may change this file's shape; until then this is a thin, honest
  placeholder rather than nothing.

Both projections are generated — never hand-edit `.claude/skills/**` or
`.codex/skills/**` directly; edit the canonical `SKILL.md` and re-run the
generator. `--check` is the drift gate a CI workflow or `horonom doctor`
(HORO-510) can call.

## Tests

`agents/common/test_project_skills.py` (stdlib `unittest`, run via
`python3 -m unittest discover -s agents/common`) covers: every canonical
skill projects successfully, `--check` detects drift after a canonical
edit, and the generator never writes outside `.claude/skills/` or
`.codex/skills/`.
