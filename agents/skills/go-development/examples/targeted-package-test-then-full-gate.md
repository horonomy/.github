# Example — targeted package test, then full gate

Scenario: fixing a bug in a Go module's backoff package where
`ParseRetryAfter` mishandles a `Retry-After` header given as an HTTP-date
string instead of an integer seconds count.

## 1. Explore

```
$ go list -json ./pkg/backoff | grep -E 'GoFiles|TestGoFiles'
"GoFiles": ["backoff.go"],
"TestGoFiles": ["backoff_test.go"],
```

Confirms the package's scope: one source file, one test file. No
`go.work`/multi-module complexity here — a single `go.mod` at the repo
root covers the whole tree, so `go list ./...` from the root would have
found this package too, but the narrower query is enough to confirm
where the change lands.

## 2. Narrow

Add the missing test case, then run only that test — not the whole
module — while iterating:

```
$ go test ./pkg/backoff/... -run TestParseRetryAfter_HTTPDate -v
=== RUN   TestParseRetryAfter_HTTPDate
    backoff_test.go:31: expected 120s, got 0s
--- FAIL: TestParseRetryAfter_HTTPDate (0.00s)
FAIL
FAIL    example.com/module/pkg/backoff  0.002s
```

This is L1 detail (`-v` gives per-test file/line) — already past bare
L0 PASS/FAIL because the failure needed no further escalation to
understand: the assertion at `backoff_test.go:31` says exactly what's
wrong.

## 3. Validate / fix

`ParseRetryAfter` only parses an integer seconds count and returns the
zero value on any parse error, silently swallowing the HTTP-date case.
Fix `backoff.go` to also try `http.ParseTime`-style parsing, then run
`go vet` scoped to the same package before re-running the test — vet and
test check different things, so both are run, not one in place of the
other:

```
$ go vet ./pkg/backoff/...
$ go test ./pkg/backoff/... -run TestParseRetryAfter_HTTPDate -v
=== RUN   TestParseRetryAfter_HTTPDate
--- PASS: TestParseRetryAfter_HTTPDate (0.00s)
PASS
ok      example.com/module/pkg/backoff  0.002s
```

L0 PASS is sufficient here — the fix targets exactly the case the new
test exercises, and the result matches expectation. No further
escalation needed.

## 4. Full Gate

The package-scoped test proves the fix works for this package; it does
not prove no other package's build or behavior broke, and this module
ships no build tags or CGO code touched by this change, so no
tagged/cross-platform variant applies here. Before calling the change
done, run the module-wide gate:

```
$ go build ./...
$ go vet ./...
$ go test ./...
ok      example.com/module/pkg/backoff       0.004s
ok      example.com/module/pkg/httpclient     0.061s
ok      example.com/module/cmd/worker         0.112s
...
ok      example.com/module/...                (all packages)
```

Only after the module-wide build, vet, and test all pass is the change
considered validated. `pkg/httpclient` imports `pkg/backoff` and has its
own tests exercising `ParseRetryAfter` indirectly through a live-request
mock — the package-scoped run never touched that path; only the full
gate did.

## Why this ordering matters

Running `go build ./...`/`go test ./...` on every edit during the fix
would waste time rebuilding/retesting packages untouched by the change
while the fix was still being worked out. Skipping the full gate at the
end would trade that saved time for a real risk that `pkg/httpclient`'s
indirect usage regressed unnoticed. The loop front-loads the fast,
package-scoped signal and reserves the module-wide build/vet/test for the
point where it actually gates the decision to call the work done — the
same "minimum waste under correctness" principle `engineering-loop`
defines, applied to Go's package-scoped vs. whole-module tooling.
