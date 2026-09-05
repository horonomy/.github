# Public Release Surface Contract (HORO-512)

A product's public presence is reconciled across every surface that could
diverge from it — never asserted from one place (Jira status,
`metadata/company.yaml`, or a hand-typed claim) and trusted elsewhere. This
contract is implemented by `scripts/public_release_reconcile.py` and
exercised through the `public-release-reconcile` skill
(`agents/skills/public-release-reconcile/SKILL.md`).

## States

Every surface, and the product overall, resolves to exactly one of:

| State | Meaning |
|---|---|
| `VERIFIED` | Checked live/mechanically and confirmed correct. |
| `REQUIRED` | The product's claimed maturity implies this surface should exist/agree, and it currently doesn't — action needed. |
| `DEFERRED` | Not required yet at this product's current maturity, and correctly absent (e.g. no company-catalog entry for an experimental product) — this also covers an `experimental` product simply not (yet) mentioned on `org_profile`/`horonom_com`: `experimental` is a legitimately publishable tier, so its absence there is a deferred choice, never a `REQUIRED` action. |
| `NOT_APPLICABLE` | This surface doesn't apply to this product (e.g. no hosted service for a library). |
| `NOT_YET_PUBLIC` | The product is intentionally pre-public; surfaces that would expose it are correctly absent. |
| `BLOCKED_EXTERNAL` | Could not be checked for a verified external reason (network unreachable, GitHub API rate-limited) — not a defect in the product or the check. |
| `FAILED` | Checked and found genuinely wrong: broken link, stale/conflicting metadata, premature public exposure, or a claim wider than the evidence supports. |

**Precedence when computing the overall product state** (most restrictive
wins, checked in this order): any `FAILED` → overall `FAILED`; else any
`BLOCKED_EXTERNAL` → overall `BLOCKED_EXTERNAL`; else any `REQUIRED` →
overall `REQUIRED`; else any `NOT_YET_PUBLIC` → overall `NOT_YET_PUBLIC`;
else any `DEFERRED` → overall `DEFERRED`; else `VERIFIED` (with every
inapplicable surface `NOT_APPLICABLE`). A product is never "done" by
majority vote — one genuinely failed surface fails the whole reconciliation.

## Surfaces

| Surface | What it checks | How |
|---|---|---|
| `repo_metadata` | The product's GitHub repo exists and is reachable | `gh api repos/<org>/<repo>` |
| `tags_releases` | GitHub tags/Releases exist **iff** the claimed lifecycle implies a release happened | `gh api repos/<org>/<repo>/releases` — a `beta`/`release_candidate`/`available` claim with zero releases is `FAILED` (claim wider than evidence), not just missing. A release on a **private** repo does not contradict an `experimental`/`not_yet_public` claim — it isn't visible outside the org, so it's not public exposure; visibility is unknown/unreachable defaults to "public" so drift is never silently hidden |
| `website` / `docs` / `hosted_service` | The configured URL, if any, responds successfully over HTTPS | live HTTP(S) request; connection failure classifies as `BLOCKED_EXTERNAL`, a non-2xx/3xx response as `FAILED` |
| `domain_tls` | TLS handshake succeeds for any configured HTTPS surface | folded into the website/docs/hosted_service checks |
| `company_registry` | `metadata/company.yaml`'s catalog entry, if any, matches the claimed lifecycle | direct read of `metadata/company.yaml` — a mismatched or stale entry is `FAILED`, a genuinely absent entry for a not-yet-warranted product is `DEFERRED` |
| `org_profile` | `profile/README.md` mentions the product **iff** its maturity requires public listing | text presence check, three-way by lifecycle: `not_yet_public` mentioned is `FAILED` (premature exposure), absent is `NOT_YET_PUBLIC`; `experimental` is a legitimately publishable tier — mentioned is `VERIFIED`, absent is `DEFERRED` (never forced); `beta`/`release_candidate`/`available` mentioned is `VERIFIED`, absent is `REQUIRED` |
| `horonom_com` | `horonom.com`'s live content mentions the product **iff** required | live fetch of `https://horonom.com`; same three-way logic as `org_profile` |
| `cross_links` | If both `website` and `docs` are configured and `VERIFIED`, the website actually links to the docs host | live fetch + substring check; otherwise `NOT_APPLICABLE` — there's nothing to cross-link |
| `product_truth` | The product's own repo is the authoritative source for its capability claim (`governance/releases/public-surfaces.md`) | proxied by `repo_metadata` succeeding — this contract does not attempt semantic verification of the claim's content itself |

## Never force a product public

A product's own repo, not this contract, decides whether it's ready to be
public. Running reconciliation against a `not_yet_public`/`experimental`
product never creates a public entry to "complete" the contract — an
absent `org_profile`/`horonom_com`/`company_registry` entry for such a
product is `NOT_YET_PUBLIC`/`DEFERRED`, a correct and final answer, not a
`REQUIRED` action item to go create one.

## Evidence required for `VERIFIED` — no completion from Jira status alone

Every `VERIFIED` verdict in this contract comes from a live check or a
direct read of a real file (`metadata/company.yaml`, `profile/README.md`,
a fetched URL) — never from a Jira ticket's status field, a comment, or an
unchecked assertion in an evidence config. `scripts/public_release_reconcile.py`
has no code path that lets a caller simply declare a surface `VERIFIED`.

## Repair mode

`scripts/public_release_reconcile.py` is **read-first**: it reports state,
it does not repair anything itself. A future bounded-repair mode (e.g.
auto-adding a missing `company_registry` catalog entry) must never perform
an irreversible or public-exposure change — publishing something, flipping
a product from `not_yet_public`, or editing `org_profile`/`horonom_com` —
without the owner decision that change actually requires
(`governance/workspace/autonomous-execution.md`'s stop rule). Until such a
mode exists, every repair is a normal PR through the usual review process.

## Product evidence config

Each product reconciled against this contract has a small config file at
`metadata/release-evidence/<product>.yaml`:

```yaml
product: horologium
claimed_lifecycle: experimental   # experimental | beta | release_candidate | available | not_yet_public
github:
  org: horonomy
  repo: horologium
website: null        # or a URL
docs: null            # or a URL
hosted_service: null  # or a URL
```

`claimed_lifecycle` is the product's own claim (from its README/repo), not
this contract's verdict — the contract checks whether the *rest of the
world* is consistent with that claim, it doesn't invent the claim itself.
