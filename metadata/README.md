# `metadata/` — Horonomy company metadata registry

`company.yaml` is the **single public source of truth** for Horonomy
mother-company facts (AAASM-5520): the company display name and canonical
website, the public company contact addresses Horonomy intentionally publishes,
the company's own structured security-response targets, and a public product
**catalog**.

## Ownership boundary

This registry owns **only company-level facts**. It references each product at
**catalog level** (name / website / GitHub org / lifecycle) and deliberately
does **not** redefine AI Agent Assembly product detail — product contact
addresses, mail domains, sender identities, security SLAs, SDK URLs, or package
identifiers. Those are owned by the AI Agent Assembly product registry in
[`ai-agent-assembly/.github`](https://github.com/ai-agent-assembly/.github/blob/main/metadata/org-profile.yaml)
(ADR 0014 / AAASM-5519).

| Layer | Owns | Not owned here |
|---|---|---|
| **This company registry** (`company.yaml`) | company name/website, published company contacts, company security SLAs, product catalog entries | product-level contact/mail/SLA/SDK/package detail |
| **AI Agent Assembly product registry** | all AA product detail | company-level facts |
| **Private runbook / secret manager** | recovery/admin identities, account IDs, secrets | never in this registry |

`hello@horonomy.dev` (website footer) and `security@horonomy.dev` (this repo's
SECURITY.md) are the only currently-published company contacts, so they are the
only ones listed — nothing is invented. `horonomy.dev` is Horonomy's own live
company domain; it is unrelated to the AI Agent Assembly product's
`agent-assembly.com` host and is not being migrated.

## Generated artifacts

`scripts/generate_company_metadata.py` derives, from `company.yaml`:

- **`generated/company.json`** — the machine-readable projection cross-repo
  consumers (e.g. `horonomy/official-website`) read instead of hand-copying a
  value. It is the pinned distribution artifact. **Generated — do not edit.**
- **`../SECURITY.md`** — the `<!-- BEGIN GENERATED: company_contact -->` region
  (reporting address + structured SLAs).
- **`../profile/README.md`** — the `<!-- BEGIN GENERATED: company_footer -->`
  region (company site + security-policy contact line).

## Changing a value

1. Edit `company.yaml`.
2. Regenerate: `python3 scripts/generate_company_metadata.py`.
3. Commit the registry change and the regenerated artifacts together.

The generator validates the schema and fails closed on malformed or
leakage-prone input; the drift gate
(`.github/workflows/company-metadata-drift.yml`) runs it in `--check` mode.
Unit tests live in `scripts/test_company_metadata.py`.

## `governance.yaml`

Separate from the company registry above: `governance.yaml` carries a
`governance_version` marker consumed by `horonom doctor` (HORO-510) to
detect drift in a product repo's projected copy of `governance/**`
content. See [`../governance/README.md`](../governance/README.md).
