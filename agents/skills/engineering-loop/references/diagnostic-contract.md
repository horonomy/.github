# Reference — the L0–L3 progressive diagnostic contract

Every check `engineering-loop` runs (test, lint, build, type-check) must be
able to report at four levels of detail, chosen by how much detail is
actually needed to settle the question in front of you — never more,
never less.

## The ladder

- **L0 — PASS/FAIL + counts.** The minimum viable answer: did it pass, how
  many things ran, how many failed. This is the default level for a
  routine narrow check that passes for the expected reason. Example:
  `47 passed, 0 failed, 2 skipped`.
- **L1 — file/line + diagnostic.** One line per failure: file, line,
  diagnostic message. Enough to know *where* without yet knowing *why*.
  Example: `tests/test_auth.py:88: AssertionError: expected 401, got 200`.
- **L2 — relevant traceback/compiler context.** The surrounding
  stack/compiler output needed to understand *why* — the failing frame(s),
  the assertion's actual vs. expected values, the compiler's full
  diagnostic block (not just its summary line). This is the level most
  real debugging happens at.
- **L3 — complete raw output, on demand.** The full unfiltered stdout/
  stderr of the run, including everything L0–L2 summarized away. Always
  reachable, never the default noise level.

## Non-negotiable rules

1. **Raw evidence is always recoverable.** Whatever summarization happens
   at L0–L2, the complete raw output must still exist somewhere (a file,
   an unmodified captured stream) so that dropping to L3 is a lookup, not
   a re-run-and-hope. Never summarize by discarding the source.
2. **Exit status is always preserved and reported alongside any summary.**
   A summarizer/wrapper must never swallow or override the underlying
   tool's exit code — the exit code is the ground truth of pass/fail, and
   L0's PASS/FAIL claim must be derived from it, not from parsing text
   that could itself be wrong.
3. **Parser/wrapper failure must never present as PASS.** If the
   summarizing layer itself errors, times out, can't parse the tool's
   output, or the underlying tool crashes/is killed before finishing, the
   result is FAIL (or UNKNOWN, surfaced loudly) — never a silent PASS by
   default. A missing or malformed result is not evidence of success.
4. **No blind `tail -N` truncation that can discard the root cause.**
   Truncating raw output to the last N lines is exactly the failure mode
   that hides the real error when the actual exception is buried mid-log
   (e.g. under a long successful setup phase, or above a misleading final
   "cleanup failed" line). Any truncation must be evidence-aware — keep
   the failing frames/diagnostics, not just "whatever came last" — or
   escalate to L3 instead of guessing what to cut.
5. **Successful, repetitive output may be collapsed aggressively.** The
   asymmetry is deliberate: collapsing 500 identical `PASS` lines into a
   count is safe and desirable (nothing informative is lost); collapsing
   or truncating output *around a failure* is not, because the discarded
   part is exactly where the root cause tends to live.

## Escalation discipline

Escalate one level at a time, only as far as the question in front of you
requires. Jumping straight to L3 on every check defeats the purpose of a
progressive contract (drowning the loop in noise); stopping at L0 when the
result contradicts your expectation defeats its safety purpose (declaring
victory on a result you don't actually understand). See
`examples/escalation-from-compact-to-raw.md` for a worked case that climbs
the full ladder.

## Reusable helpers

The ladder above is not just prose to re-derive by hand each session -
two small, deterministic, unit-tested scripts under `scripts/` implement
it (HORO-971):

- **`scripts/diagnostic_compact.py`** - classifies a tool's raw output +
  exit code into the L0-L3 ladder and renders whichever level you ask
  for. Enforces all five non-negotiable rules above in code, not just
  documentation: `passed` is derived from the exit code alone (never from
  parsed text), a parser that raises degrades to an unstructured result
  instead of crashing or silently reporting PASS, and `--level 3` always
  returns the complete, unmodified raw text. Supports `pytest`, `cargo`
  (build errors and test panics), and `vitest` output today, plus a
  generic line-scanning fallback for anything else. Two entry points:
  `run -- <command...>` (execute a real command and classify it live) and
  `classify --exit-code N [--raw-file PATH]` (classify previously
  captured output, offline). Tests: `test_diagnostic_compact.py`.
- **`scripts/efficiency_eval.py`** - the standing Agent Efficiency Eval
  harness: scores RAW vs compact bytes and confirms exit-code/failure-
  signal preservation, either against the bundled deterministic
  `fixtures/*.json` captures (Python/pytest, Rust/cargo, TypeScript/
  Vitest - `run-all`) or against a real repo + command (`live --cwd DIR
  -- <command...>`). This replaces the one-off manual RAW-vs-compact
  session that originally motivated this contract with something
  re-runnable. Tests: `test_efficiency_eval.py`.

Both scripts are stdlib-only and follow the same CLI/testing conventions
as `repo-scaffold/scripts/repo_scaffold.py` and
`credential-operations/scripts/probe_env_presence.py` - see those for the
house style if extending this further.
