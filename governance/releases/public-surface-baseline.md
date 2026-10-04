# Public surface baseline (HORO-1699)

Product repositories may run `scripts/public_surface.py` after generating a
static site. The checker validates shared boundaries while the product keeps
its own copy, constitution, runtime, and navigation decisions.

Create a small JSON manifest in the product repository and invoke:

```sh
python3 .github/scripts/public_surface.py public-surface.json site/dist
```

Run it after the product's own docs build, for example:

```yaml
- run: npm run build
- run: python3 .github/scripts/public_surface.py public-surface.json site/dist
```

The check is appropriate for company products with an established public
lifecycle (`beta`, `release_candidate`, or `available`) and for OSS projects
whose repository is the canonical project surface. It does not promote an
`experimental` or `not_yet_public` product; lifecycle and release evidence
remain governed by the Public Release Surface Contract.

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

The first adoption set can use the same checker with only manifest values
changed: Libra (`https://libra.horonom.com`, `/docs`), Eltanin
(`https://eltanin.horonom.com`, `https://docs.eltanin.horonom.com`), and
Glomeris (`https://chisanan232.github.io/glomeris/`, `/glomeris/docs`). These
are examples of two company surfaces and one GitHub Pages project surface;
they do not imply that any domain or lifecycle gate has been approved.

## Adoption checklist

- [ ] Define `public-surface.json` beside the product's site build.
- [ ] Set the canonical company or GitHub Pages URL actually deployed.
- [ ] List only product-owned navigation links and the intentional docs path/host.
- [ ] Declare analytics payload field names and verify they contain no sensitive data.
- [ ] Run the checker against the exact generated output in CI.
- [ ] Capture external DNS/TLS/redirect evidence separately after deployment.
- [ ] On rollback, restore the last verified artifact and keep this check enabled; investigate or fix the failing artifact before the next rollout.
