<!-- horonom:generated -->
<!-- Source: horonomy/.github agents/skills/typescript-development/SKILL.md. Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->

# SKILL.md — typescript-development

## Purpose

Give TypeScript/JavaScript repos the concrete pnpm/typecheck/Vitest
commands that `engineering-loop` (HORO-969 §1) needs to fill its Explore →
Narrow → Validate → Escalate → Full Gate stages. This skill owns the
commands; `engineering-loop` owns the loop and diagnostic discipline
around them — it always composes with that skill, never replaces it.

## Type

Auto-used. Applicability is repo-evidence-based (see `manifest.yaml`):
a `package.json` with TypeScript dependencies or a `tsconfig.json`.

## When to use

- Any TypeScript/JavaScript repo — or the touched surface of a monorepo
  package — that carries a `tsconfig.json` and a pnpm-managed
  `package.json`.
- Iterating on a TS module, its tests, its types, or its build output.

## When NOT to use

- A repo with no `tsconfig.json`/TS deps (plain JS with no type layer) —
  the Vitest/pnpm guidance still applies loosely, but the non-negotiable
  typecheck rule below has nothing to bind to.
- As a substitute for `engineering-loop`'s Full Gate discipline — a
  targeted Vitest pass is Narrow-stage evidence, never release evidence on
  its own.
- A napi-rs/native-binding repo's Rust side — that's `rust-development`'s
  surface; this skill covers only the TypeScript side of that boundary.

## Non-negotiable rules

- **Never replace a project-aware typecheck with an ad-hoc single-file
  check.** `tsc -p <real tsconfig>` (or the package's own `typecheck`
  script wrapping it) is the only valid typecheck. Running `tsc
  path/to/file.ts` directly discards the project's `compilerOptions`,
  path aliases, and module resolution — it can pass while the real
  project-aware build fails, and that gap is exactly the failure mode this
  rule exists to prevent.
- **Never enable `skipLibCheck`, weaken `strict`, or loosen any other
  strictness flag merely to make a check pass faster or silence noise.**
  If a check is slow, narrow *what* you run (see `references/`), not *how
  strict* it is.

## Routing decision

1. Detect the package manager and workspace shape first — see
   `references/pnpm-typecheck-workflow.md` §1. Don't assume every nested
   `package.json` belongs to the root workspace.
2. During the edit loop (engineering-loop's Narrow stage), run Vitest
   scoped to the file/directory/test-name under change —
   `references/pnpm-typecheck-workflow.md` §2 — never the whole suite.
3. When a change touches types (new exports, signature changes, generic
   constraints), run the project-aware typecheck command scoped to the
   affected package before moving on — §3.
4. Before Full Gate: run the full project-aware `tsc` build, the full
   Vitest suite, and the package's build/pack validation appropriate to
   its ESM/CJS/library/web/service shape — §4.

## Optional-tool fallback

RTK/CodeGraph accelerate Explore/Narrow the same as any other stack — see
`agents/skills/engineering-loop/references/optional-tool-fallback.md`. If absent,
fall back to `rg`/`grep` + `Read`, or the editor-integrated TS language
service (`tsserver`) for cross-references. Neither replaces the actual
`tsc`/Vitest/build gates.

## References

- `references/pnpm-typecheck-workflow.md` — package/workspace detection,
  targeted Vitest, project-aware typecheck usage, pnpm filter patterns,
  build/package validation per module shape.

## Worked example

- `examples/targeted-vitest-then-full-gate.md` — a failing test found by a
  targeted Vitest run, fixed, then validated with the full `tsc -p .` +
  full Vitest + build gate.

## Composition

- napi-rs / native-binding repos (a `native/` directory built with
  `napi build`, a Rust `Cargo.toml` alongside the TS package) additionally
  compose `rust-development` for the native side, and treat the generated
  `.node`/`.d.ts` binding as an integration boundary to validate on both
  sides of a change, not just the TS side.
