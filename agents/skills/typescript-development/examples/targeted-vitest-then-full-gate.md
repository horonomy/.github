# Example — targeted Vitest, then the full gate

Scenario: fixing a bug in a TypeScript SDK package where
`parseRetryAfter()` mishandles a `Retry-After` header given as an
HTTP-date string instead of an integer seconds count. The package is a
workspace member (`packages/http/`) inside a pnpm workspace, built as a
dual ESM/CJS library.

## 1. Explore

```
$ codegraph explore "parseRetryAfter"
```

Returns the function's source (`packages/http/src/backoff.ts:18-34`) and
its two call sites (`packages/http/src/client.ts:96`,
`packages/http/src/backoff.test.ts:12`). CodeGraph is available in this
repo, so this replaces a grep + Read round trip. If it weren't available,
`rg "parseRetryAfter" packages/http/src` plus `Read` would do the same job
more slowly.

## 2. Narrow — write the test, run it scoped

```ts
// packages/http/src/backoff.test.ts
it("handles an HTTP-date Retry-After value", () => {
  expect(parseRetryAfter("Wed, 21 Oct 2026 07:28:00 GMT")).toBeGreaterThan(0);
});
```

Run only this file, scoped to the workspace package:

```
$ pnpm --filter @horonom/http vitest run src/backoff.test.ts
 ❯ src/backoff.test.ts (5 tests | 1 failed)
   ✓ parses an integer seconds value
   ✓ returns null on garbage input
   ✓ returns null on empty string
   ✓ clamps negative values to zero
   × handles an HTTP-date Retry-After value
     → expected null to be greater than 0

 Test Files  1 failed (1)
      Tests  1 failed | 4 passed (5)
```

This is L0: PASS/FAIL + counts, exactly as expected — the new test exists
specifically to prove the bug.

## 3. Validate / fix

Escalate briefly to L1 to see the actual assertion detail (already visible
above: `expected null to be greater than 0`) and confirm it's the intended
failure, not a typo in the test. It is — `parseRetryAfter` only handles
the integer-seconds branch and falls through to `null` for a date string.

Fix `backoff.ts` to also parse the HTTP-date form via `Date.parse`, then
re-run the same narrow check:

```
$ pnpm --filter @horonom/http vitest run src/backoff.test.ts
 ✓ src/backoff.test.ts (5 tests) 4ms

 Test Files  1 passed (1)
      Tests  5 passed (5)
```

Because the fix touched a function's return-type-relevant branch (a new
`Date`-based code path), also run the project-aware typecheck scoped to
the package before moving on — not a bare `tsc src/backoff.ts`, which
would skip the package's `tsconfig.json` path aliases and `strict` flags
entirely:

```
$ pnpm --filter @horonom/http exec tsc -p tsconfig.json --noEmit
```
(clean — no output, exit 0)

L0 PASS on both is sufficient here — the fix targets exactly the case the
new test exercises, and the type surface didn't actually change shape.

## 4. Full Gate

The narrow checks prove the fix works and the package's own types still
hold; they do not prove nothing else in the workspace broke, and they are
not the repo's authoritative release signal. Before calling this done, run
the full gate:

```
$ pnpm -r run typecheck
Scope: 6 of 6 workspace projects
@horonom/http typecheck$ tsc -p tsconfig.json --noEmit
@horonom/http typecheck: Done
... (5 more packages, all clean)

$ pnpm -r vitest run
 Test Files  42 passed (42)
      Tests  318 passed (318)

$ pnpm --filter @horonom/http run build
@horonom/http build:esm$ tsc -p tsconfig.build.json
@horonom/http build:cjs$ tsc -p tsconfig.cjs.json && node ./scripts/write-cjs-package-json.mjs
@horonom/http build: Done

$ node -e "require('./packages/http/dist/cjs/index.js')"
$ node --input-type=module -e "import('./packages/http/dist/esm/index.js').then(() => console.log('ok'))"
ok
```

Only after the full typecheck, full Vitest run, and both build+load smoke
checks pass is the change considered validated. If the targeted test had
been the only check run before opening a PR, a regression in an unrelated
consumer of `parseRetryAfter` elsewhere in the workspace — or a broken
`exports` map entry that `tsc --noEmit` alone can't see — would have
shipped unnoticed.

## Why this ordering matters

Running `pnpm -r vitest run` and a full dual build on every edit would
waste real time while the fix was still being worked out — a workspace
this size takes tens of seconds per full pass versus milliseconds for the
one scoped test file. Skipping the full gate at the end would trade that
saved time for a real risk of shipping a workspace-wide regression or a
broken published artifact. The loop front-loads the fast, targeted signal
during iteration and reserves the expensive, comprehensive signal for the
point where it actually gates the decision to call the work done.
