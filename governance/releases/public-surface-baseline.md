# Public surface baseline (HORO-1699)

Product repositories may run `scripts/public_surface.py` after generating a
static site. The checker validates shared boundaries while the product keeps
its own copy, constitution, runtime, and navigation decisions.

Create a small JSON manifest in the product repository and invoke:

```sh
python3 .github/scripts/public_surface.py public-surface.json site/dist
```

The manifest must declare `base_url`, and may declare `docs_url`,
`required_navigation`, and an `analytics` object. `docs_url` may be a
same-host path (for example `/docs`) or an HTTPS host. The generated artifact
must contain an HTTPS canonical link, paired `robots.txt`/`sitemap.xml`, and
navigation links named by the product. The check fails closed on encoded path
traversal, private or internal origins, common secret patterns, sensitive
query parameters, cross-host sitemap entries, and analytics payload fields
that could contain prompts, identities, repository names, tenant data,
secrets, or authenticated content.

Adoption is intentionally opt-in and local to each repository. A company
product can use `<product>.horonom.com` plus either `docs.<product>.horonom.com`
or same-host `/docs`; an OSS project can use its GitHub Pages project path.
Runtime/API hosts remain outside this static artifact contract. Run the
product's normal build, link/security checks, then this offline check in CI.

## Adoption checklist

- [ ] Define `public-surface.json` beside the product's site build.
- [ ] Set the canonical company or GitHub Pages URL actually deployed.
- [ ] List only product-owned navigation links and the intentional docs path/host.
- [ ] Declare analytics payload field names and verify they contain no sensitive data.
- [ ] Run the checker against the exact generated output in CI.
- [ ] Capture external DNS/TLS/redirect evidence separately after deployment.
- [ ] On rollback, remove or disable the workflow invocation before changing product content.
