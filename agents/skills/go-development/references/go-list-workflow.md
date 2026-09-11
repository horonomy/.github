# Reference — `go list`, targeted test/vet, and build-boundary caveats

## Package and workspace discovery

Establish scope before running anything:

```
$ go list ./...                     # every package in the current module
$ go list -m all                    # resolved module graph (this module + deps)
$ go list -deps ./pkg/foo           # everything pkg/foo imports, transitively
$ go list -json ./pkg/foo           # structured detail: GoFiles, Imports, TestGoFiles, Deps
```

For "what imports this package" (the direction `go list` doesn't answer
directly), use `go list -f` over the whole module and filter, or reach for
CodeGraph/an IDE's reference-finder as the accelerator
(`agents/skills/engineering-loop/references/optional-tool-fallback.md` — same
preferred→fallback contract applies to Go's own tooling as to RTK/
CodeGraph):

```
$ go list -f '{{.ImportPath}}: {{.Imports}}' ./... | grep 'pkg/foo'
```

If a `go.work` exists, run `go list` from the workspace root so it spans
every declared module. A monorepo with multiple independent `go.mod`
files and no `go.work` requires running discovery once per module — a
`go list ./...` inside one module never sees a sibling module's packages.

**This is navigation, not proof.** `go list`'s import graph tells you
which packages are structurally connected and where to look next. It
never tells you whether a change actually breaks a caller at compile time
or behavior time — only `go build`/`go vet`/`go test` settle that.

## Targeted iteration during the edit loop (Narrow)

Scope every command to the package(s) actually under change, not the
whole module, while iterating:

```
$ go test ./pkg/backoff/... -run TestParseRetryAfter -v
```

- `-run <regex>` matches test *names*, not file names — anchor it
  (`-run '^TestParseRetryAfter$'`) if a looser pattern would also match an
  unrelated test.
- `-v` gives L1-equivalent detail (per-test PASS/FAIL and log output)
  without the full L3 raw dump; use plain (non-`-v`) output for L0
  PASS/FAIL + counts, and add `-race`/full stdout capture only when
  escalating further (L2/L3), per `engineering-loop`'s diagnostic
  contract.
- Package-scoped `go vet ./pkg/backoff/...` once the package compiles
  catches suspicious constructs (`Printf` format mismatches, struct tag
  errors, lock-copying) that a passing test does not — run it as a
  distinct check, not a substitute for `go test`.

## Build/binary validation across an executable boundary

A package-scoped `go test` pass does not prove a `cmd/`-level binary
still builds or that CGO/native code compiled and linked correctly. Add
an explicit build/run step whenever the change crosses one of these
boundaries:

- **Executable boundary**: the change touches or is reachable from
  `cmd/<binary>/main.go` → `go build ./cmd/...` (or the repo's specific
  binary path) before considering the change validated, even if
  `go test ./...` is green — `main` packages often have no test coverage
  of their own wiring.
- **CGO/native boundary**: the change touches a file behind
  `import "C"` or a CGO-only build tag (e.g. a vendored native shim
  invoked via `-tags <tag>`) → build and test with `CGO_ENABLED=1` and the
  relevant tag explicitly, since the default `go test ./...` run may not
  exercise that code path at all:

  ```
  $ CGO_ENABLED=1 go test -tags aa_ffi_go ./...
  ```

  If the native side requires its own build step first (a vendored Rust/C
  shim compiled via `cargo build`/`make`/similar), run that step before
  the tagged Go build — a stale or missing native artifact produces a
  link error, not a Go compile error, and is easy to misdiagnose as a Go
  bug.

## Build-tags and platform caveats

**A default host test run is not proof of correctness for code gated
behind a non-default build tag or a different platform/GOOS/GOARCH.**
Concretely:

- Files behind `//go:build linux` / `//go:build windows` / a custom tag
  like `aa_ffi_go` are silently excluded from an untagged `go test ./...`
  on a host that doesn't match — a green default run says nothing about
  that file's correctness. Identify tagged files (`grep -rl 'go:build'`
  or `go list -json` per package, which reports `IgnoredGoFiles`) and run
  the tagged/cross-platform build explicitly when the change touches one:

  ```
  $ GOOS=windows GOARCH=amd64 go build ./...       # cross-compile check
  $ go build -tags <tag> ./...                     # tag-gated file check
  ```

  A cross-compiled `go build` proves the code compiles for that
  target — it does not run its tests there. Treat cross-compilation as a
  compile-time signal only, not a substitute for a real test run on that
  platform (native fallback: CI's matrix job, a VM, or a device, per
  `engineering-loop`'s optional-tool-fallback contract for
  platform-specific evidence).
- Never assume a single host's default `go test ./...` is the module's
  Full Gate when the module ships platform- or tag-gated code — the Full
  Gate for such a module is the union of the default run plus each
  tagged/cross-platform variant the change actually touches.

## When `go mod tidy` is appropriate

Run `go mod tidy` only as a deliberate, isolated step tied to an actual
dependency change (adding/removing an import, bumping a version) — never
as an incidental side effect of an unrelated edit loop:

- **Appropriate**: after adding a new import that pulls in a new
  dependency, after removing the last usage of a dependency, after
  editing `go.mod`/`go.sum` directly, or as an explicit dependency-hygiene
  task.
- **Not appropriate**: running it "just in case" at the end of an
  unrelated bugfix or feature change — it can rewrite `go.sum` and prune
  or add indirect requirements unrelated to the change under review,
  which muddies the diff and the review. If `go mod tidy` produces a
  diff during an unrelated change, that is a signal to investigate why
  (a stray import, a drifted `go.sum`) before committing it, not to
  fold it in silently.
- Always run it as its own reviewable step (its own commit, if the
  broader workflow calls for atomic commits) so a reviewer can see
  exactly which dependency changed and why.
