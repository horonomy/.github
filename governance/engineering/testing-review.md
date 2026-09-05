# Testing and review invariants

Detailed rule text for `governance/README.md`'s Company layer.

## Testing

- New features get tests; bug fixes get a regression test.
- Tests are deterministic, isolated, and fast — no reliance on external
  network state or execution order.
- Test behavior, not implementation. A test that only re-asserts a private
  internal is brittle without adding confidence.
- Never disable or delete a failing test to make CI green. Fix it, or
  escalate with the failure documented.
- Run the tests impacted by a change during iteration; run the full suite
  (per the repo's own `CLAUDE.md`/CI config) before opening a PR.

## Review

- At least one approval is required before merge, except the narrowly
  scoped solo-maintainer admin-merge bypass in
  [`git-pr-merge.md`](./git-pr-merge.md).
- **An implementation sub-agent must never merge its own PR.** The
  orchestrating agent (or a human reviewer) is the independent reviewer of
  record and runs the pre-merge review checklist in
  [`git-pr-merge.md`](./git-pr-merge.md) before every merge — never a
  rubber stamp of a sub-agent's self-report.
- Address every reviewer comment (human or automated) before merging;
  resolve or reply to every open thread.
