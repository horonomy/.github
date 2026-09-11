# Reference — pnpm / project-aware typecheck / Vitest workflow

## 1. Package and workspace detection

Before running anything, establish what you're actually inside:

- Check for a root `pnpm-workspace.yaml`. Its presence is what makes a
  repo a pnpm **workspace** — a nested `package.json` does not
  automatically belong to it. A monorepo-shaped directory tree with no
  `pnpm-workspace.yaml` (e.g. an app that happens to vendor a few
  sub-packages, or a plain multi-`package.json` layout managed some other
  way) is not a workspace; treat each `package.json` as its own project
  and run commands from that directory, not with `pnpm --filter`.
- When `pnpm-workspace.yaml` exists, find the specific package you're
  touching: `pnpm -C <package-dir> exec pwd` or simply read the nearest
  ancestor `package.json`'s `name` field. Never assume the repo root
  `package.json` scripts apply to a package with its own scripts —
  workspace packages routinely override `test`/`typecheck`/`build`.
- Confirm the lockfile matches: `pnpm-lock.yaml` present and not stale
  relative to `package.json`/workspace member manifests
  (`pnpm install --frozen-lockfile` is the fast correctness check — it
  fails loudly instead of silently reinstalling a divergent tree).

## 2. Targeted Vitest during the edit loop

Vitest is engineering-loop's Narrow-stage tool for this stack. Scope every
iteration to the smallest thing that can prove or disprove the change:

```bash
# one file
pnpm vitest run path/to/module.test.ts

# one directory
pnpm vitest run path/to/feature/

# one test name (regex against test/describe titles)
pnpm vitest run path/to/module.test.ts -t "handles retry-after header"
```

In a workspace, scope to the package first, then the file, so you don't
accidentally pick up a same-named test in a sibling package:

```bash
pnpm --filter <package-name> vitest run path/to/module.test.ts
```

Use `--watch` (Vitest's default outside CI) only for genuinely interactive
iteration in a terminal session you're driving directly — an agent loop
should prefer the one-shot `run` form so each invocation has a clean
PASS/FAIL exit status to reason about (engineering-loop's L0).

Never reach for the whole-suite `pnpm test` / `pnpm vitest run` (no path)
as the iteration-loop command — that's the Full Gate command, not the
Narrow one.

## 3. Project-aware typecheck usage

The project-aware typecheck is whatever the package's own script wraps —
read `package.json`'s `scripts.typecheck` (or `scripts.build` if there's
no separate typecheck script) rather than guessing a `tsc` invocation:

```bash
pnpm run typecheck            # if the package defines one
# or, reading the actual config the repo uses:
pnpm exec tsc -p tsconfig.json --noEmit
pnpm exec tsc -p tsconfig.test.json --noEmit   # if tests use a separate config
```

In a workspace, scope with `--filter`:

```bash
pnpm --filter <package-name> run typecheck
```

**Never** substitute `pnpm exec tsc path/to/file.ts` (no `-p`) for any of
the above — a bare file invocation ignores the project's `tsconfig.json`
entirely: no path aliases, no `strict` flags, no `types` roots, no project
references. It can report success on code that fails the real build. If
you need a quick signal on a single file, filter the *reported*
diagnostics after a project-aware run (`grep path/to/file.ts`), don't
change *how* the file is checked.

Likewise, never add `skipLibCheck: true` or loosen `strict`/
`noImplicitAny`/`strictNullChecks`/etc. in a `tsconfig*.json` to make a
check pass faster or quiet unrelated `node_modules` type noise — that
noise is a real dependency-typing problem to fix or pin around, not a
reason to weaken the project's type contract.

## 4. pnpm workspace/filter patterns (workspace repos only)

Conditional on step 1 confirming an actual `pnpm-workspace.yaml`:

```bash
pnpm --filter <package-name> <script>              # one package
pnpm --filter "./packages/*" <script>               # glob over a directory
pnpm --filter <package-name>... <script>            # package + its dependents
pnpm -r <script>                                    # every workspace package (Full Gate only)
```

`-r`/`--recursive` is a Full Gate operation, not a Narrow one — it re-runs
the command across every package regardless of what changed.

## 5. Build/package validation by module shape

Before Full Gate, validate the actual artifact shape the package ships,
not just that `tsc` exits 0:

- **Library shipping both ESM and CJS** (dual-package): run each build
  script separately if the repo splits them (`build:esm`, `build:cjs`),
  then check the resulting `package.json` `exports` map actually resolves
  both conditions — a broken `exports` map is a real estate a passing
  `tsc` doesn't catch. `node -e "require('./dist/cjs/index.js')"` and
  `node --input-type=module -e "import('./dist/esm/index.js')"` are cheap
  smoke checks for "does this actually load."
- **Web app / frontend bundle**: run the bundler's build (`vite build`,
  `next build`, etc.) — a green `tsc` does not guarantee a clean bundle;
  bundlers apply their own resolution and tree-shaking that can surface
  issues `tsc` alone won't.
- **Service/CLI (single runtime target)**: run its build script and then
  actually execute the built entrypoint once (`node dist/index.js
  --help`, or the package's own smoke command) — a build succeeding is
  not the same as the emitted JS running.
- **Native-binding (napi-rs) package**: the TS side's build validates only
  the TS surface; validate the native binding loads (`node -e
  "require('./native/<pkg>/index.cjs')"` or the package's own native
  smoke test) as a separate step, and compose `rust-development` for
  anything on the Rust side of that boundary.

## 6. Optional-tool fallback

Same contract as `agents/skills/engineering-loop/references/optional-tool-fallback.md`:
CodeGraph/RTK accelerate finding call sites and proxying these commands
but are never required — fall back to `rg`/`grep` + `Read`, or the
editor's TypeScript language service, and to the native `pnpm`/`tsc`/
`vitest` commands run directly. A missing accelerator never means
skipping the typecheck or test run itself.
