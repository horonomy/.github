# Docs vs. ADR boundary

Detailed rule text for `governance/README.md`'s Company layer, codifying
[ADR-0005](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0005-horonom-governance-and-agent-workspace-architecture.md)
decision #10.

## When a decision needs an ADR

An ADR is required only for a decision that is **durable, cross-repo, and
touches source-of-truth, security, or a public contract**. The company ADR
series lives in
[`horonomy/internal-docs`](https://github.com/horonomy/internal-docs/tree/main/docs/engineering)
(`docs/engineering/adr-000N-*.md`) — not in this repo. Add a new entry to
that series' numbering, and link it from
[`docs/engineering/index.md`](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/index.md)
there.

## When a decision belongs in governance docs / CONTRIBUTING / CLAUDE.md / a skill instead

Everything else: workflow conventions, tool usage, day-to-day process,
routine implementation choices with a clearly-reversible answer. These
belong in `governance/**` (this repo), `CONTRIBUTING.md`, `CLAUDE.md`, or a
shared skill under `agents/skills/` — whichever already owns the
concern per `governance/README.md`'s ownership matrix. Don't create an ADR
for a decision this category already covers; don't bury a genuinely durable
cross-repo decision in a skill's README where it won't be found later.
