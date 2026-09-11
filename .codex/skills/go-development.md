<!-- horonom:generated -->
<!-- Source: horonomy/.github agents/skills/go-development/SKILL.md. Provisional Codex projection shape — see agents/common/README.md. Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->

# SKILL.md — go-development

## Purpose

Package-scoped test/vet/build workflows for Go repos and modules
(`governance/engineering/agent-skill-architecture.md`, HORO-969 §1). This
skill supplies Go's concrete commands and package-graph navigation; it
composes with `engineering-loop` for the execution model (Explore → Narrow
→ Validate → Escalate → Full Gate) and the L0–L3 diagnostic contract —
it does not duplicate either.

## Type

Auto-used. Invoke whenever iterating on Go code in a repo with `go.mod`
evidence (`governance/engineering/agent-skill-architecture.md` §3).

## When to use

- Editing, testing, or debugging Go source in a module or workspace.
- Navigating a package graph before making a change (Explore stage).
- Deciding whether `go vet`/`go build`/`go test` scope should be
  package-scoped or full-module.

## When NOT to use

- As a substitute for `engineering-loop`'s Full Gate stage — a package-
  scoped `go test ./pkg/...` pass is targeted evidence, never release
  evidence for the module as a whole.
- To justify skipping `go vet`/`go build` because `go test` passed — each
  checks a different thing (vet: suspicious constructs; build: compiles at
  all, including files no test imports; test: behavior).
- As a place to run `go mod tidy` opportunistically. See
  `references/go-list-workflow.md` for when tidy is actually appropriate.

## Discovery: modules and workspaces first

Before running anything, establish the module/workspace boundary:

- `go.mod` at the repo root → single module; `go list ./...` enumerates
  every package in it.
- `go.work` present → a multi-module workspace; `go list ./...` from the
  workspace root spans every module `go.work` declares, `go list -m all`
  lists the resolved module set.
- A monorepo may hold more than one independent `go.mod` with no
  `go.work` tying them together — check for this before assuming a single
  `go list ./...` sees everything the repo contains.

## `go list` for package-graph navigation (Explore/Narrow)

`go list` is Go's own accelerator for the Explore stage — prefer it over
cold grep for "what package is this in" / "what does this package import"
/ "what imports this package" questions. Full workflow, flags, and worked
queries: `references/go-list-workflow.md`.

**`go list`/static import-graph output is navigation evidence only —
never proof of runtime impact.** It tells you where to look and what to
run next; it never tells you whether a change is safe. The actual gate is
`go build`/`go vet`/`go test` succeeding, per `engineering-loop`'s
optional-tool-fallback contract (`agents/skills/engineering-loop/references/optional-tool-fallback.md`)
applied to Go's own native tooling, not just to RTK/CodeGraph.

## Narrow, then Full Gate

1. **Narrow**: `go test <pkg-path> -run <TestName> -v` for the one
   package/test under active iteration. `go vet <pkg-path>` scoped the
   same way once the test compiles.
2. **Validate/Escalate**: use `-v` output (L1), then `-run` plus targeted
   `t.Log`/`-race` (L2), then full raw output (L3) per the diagnostic
   contract — same ladder `engineering-loop` defines, applied to Go's
   output shapes.
3. **Full Gate**: before calling the change done, `go build ./...`,
   `go vet ./...`, `go test ./...` across the whole module/workspace — a
   passing package-scoped test never proves an unrelated caller still
   compiles or behaves correctly.

## References

- `references/go-list-workflow.md` — package/workspace discovery,
  targeted test/vet commands, build/binary validation, build-tags/CGO/
  platform caveats, and when `go mod tidy` is appropriate.

## Examples

- `examples/targeted-package-test-then-full-gate.md` — a failing test
  fixed via targeted `go test -run`, then the full build/vet/test gate.
