# Product Messaging Truth — portfolio audit (2026-10-08)

**Ticket:** [HORO-1734](https://lightning-dust-mite.atlassian.net/browse/HORO-1734), under Epic [HORO-1732](https://lightning-dust-mite.atlassian.net/browse/HORO-1732).

Founder decision basis (HORO-1732, 2026-10-07): public messaging must separate
**Product identity** (durable problem + abstraction boundary), **Current
availability** (which hosts/adapters are actually supported today), and
**Initial wedge/examples** (dogfooding/distribution use cases). Invariant:
*never describe a product by its current host integration when the
underlying problem exists independently of that host.*

This is an evidence-backed audit, not a rewrite. Each row cites the exact
file/surface read this session. Remediation PRs are tracked per product
ticket (HORO-1735–1738); this matrix records disposition, not the diff.

## Method

For each product: read the repo's own README/constitution (T1, canonical),
then its public site hero/docs-landing copy (lower authority), then its
`horonomy/.github` `metadata/release-evidence/<id>.yaml` entry (maturity) and
`horonomy/official-website` `src/data/productRegistry.ts` entry (company
projection, T7). Classify each identity-bearing sentence as: canonical
identity / concrete value / current availability / wedge-example /
maturity-limitation / unsupported-or-stale / deliberate intrinsic boundary.

## Drift matrix

| Product | Surface | Current wording (evidence) | Classification | Disposition | Ticket |
|---|---|---|---|---|---|
| Fornax | `fornax-core/README.md:3` | "Evidence-first agent-integrity system **for coding agents** (Claude Code, Codex, opencode)." | Identity sentence, incorrectly scoped to current adapters | **Fix** — identity → "AI agents" general; name the three adapters as availability, not boundary | HORO-1735 |
| Fornax | `fornax-website/index.html` `<meta description>`, `og:description` | "Fornax checks what **a coding agent** tells you against the evidence..." | Identity-bearing metadata, same narrowing | **Fix** | HORO-1735 |
| Fornax | `fornax-website/src/pages/Home.tsx` hero `<h1>` | "What should you believe about what **your coding agent** just told you?" | Identity question scoped to coding agents | **Fix** — generalize; keep "a coding agent narrates..." illustrative paragraph (~line 37) as a wedge example, not identity | HORO-1735 |
| Fornax | `fornax-website/index.html` `<title>`/`og:title` | "Fornax — evidence-first agent integrity" | Already agent-general | **No change** | — |
| Circinus | `circinus/README.md` (top) | "A local-first **Agent Trust Runtime**... Trust nothing implicitly. Analyze ahead. Never lose provenance. Enforce at the boundary." | Canonical identity, already host-agnostic | **No change** — this is the compliant pattern | — |
| Circinus | `circinus/site/docs/start-here.md` "What this product does" | "Circinus watches what **your Claude Code session** reads... and what it's about to do..." | Identity paragraph baked to one host | **Fix** — identity → "an AI agent"; cite Claude Code as current host, pointing at limitations.md's pattern | HORO-1736 |
| Circinus | `circinus/site/docs/limitations.md` | "a runtime for every agent framework — **Claude Code only, today**" | Correctly-scoped availability/limitation | **No change** — reference pattern to generalize elsewhere | — |
| Ophiuchus | `ophiuchus/README.md:3` | "Stop rebuilding context every time work crosses a developer, agent, machine or organizational boundary." | Canonical callout, already host-agnostic | **No change** | — |
| Ophiuchus | `ophiuchus/README.md:5-7` | "Ophiuchus is a **Context Fabric**: it moves the useful part of an **AI-assisted engineering session** across machines and across **coding-agent tools**..." | Identity paragraph narrowed to engineering/coding-agent sessions | **Fix** — identity → "AI-assisted work" generally; coding-agent tools → named current wedge | HORO-1738 |
| Ophiuchus | `ophiuchus/website/src/pages/index.tsx:122` hero `<h1>` | "Hand off AI-assisted work without rebuilding the context" | Already agent-general | **No change** — reference pattern | — |
| Libra | `libra-governor/README.md:5-6`, `PRODUCT.md` "What Libra is" | "a data-plane daemon that sits between an **agentic coding tool** (Claude Code, Codex, etc.) and the LLM provider..." | North-Star-level identity narrowed to "coding tool"; host parenthetical is correctly-patterned availability | **Fix** — identity → "agentic work" per founder decision; keep "(Claude Code, Codex, etc.) today" as availability | HORO-1737 |
| Libra | `libra-governor/PRODUCT.md` North Star / supporting invariant | "Never start work you are unlikely to afford to finish." + estimate/admit/track/replan invariant | Already host-agnostic | **No change** | — |
| Horologium | `horologium/README.md` | "Self-hosted **Product Truth & Integrity** platform... exposes it... to humans (Web dashboard) and **coding agents (MCP)**" | Identity is about software-product truth generally; "coding agents (MCP)" is a named *consumer/access channel*, not the product boundary | **No change** — already compliant with "preserve, don't collapse into coding-agent observability" | — |
| Horologium | `horologium/site/public/index.html` hero `<h1>` | "Which one is the product?" | Agent-general | **No change** | — |
| Octans | `horonom-site/src/data/productRegistry.ts` (`id: octans`) | "Verifies a change is safe to ship before it reaches production, across distributed services." | Deliberate intrinsic domain (software change-safety) — not an agent product at all | **No change** — do not generalize for portfolio symmetry (explicit epic non-goal) | — |
| Eridanus | `dot-github/metadata/release-evidence/eridanus.yaml` | `claimed_lifecycle: experimental`, `website: null`, no public surface | No public surface exists to audit | **N/A** — tracked as not-yet-public; nothing to remediate until HORO-1695 publishes it | — |

## Conflicts requiring escalation

None found. Every drift row above traces to the founder's own HORO-1732
canonical-identity list; no two authoritative sources disagree about where
each product's boundary is. (Libra's `PRODUCT.md` North Star predates the
founder's 2026-10-07 decision and is narrower than it, which is the kind of
"conflicting evidence" the epic's non-goal #7 anticipates as grounds to
revisit a North Star — the founder decision is the newer, higher-precedence
source, so `PRODUCT.md` is corrected to match it, not escalated.)

## Not yet audited in this pass

Company-layer convergence (Horonom corporate card / Product Atlas / SEO
metadata vs. each product's corrected source) is HORO-1739's job and depends
on HORO-1735–1738 landing first (T7 may only simplify a corrected T1 fact,
never originate one — see `horonom-site/design/product-narrative-hierarchy.md`
§8). Revisit this matrix's "No change" rows after HORO-1740's guard exists,
since a guard can surface drift a manual read missed.
