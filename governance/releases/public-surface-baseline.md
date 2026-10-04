# Public surface baseline (HORO-1699)

Product repositories may run `scripts/public_surface.py` after generating a
static site. The checker validates shared artifact boundaries while the
product repository keeps its copy, design constitution, runtime behavior,
manifest, and navigation decisions.

Create a small JSON manifest in the product repository and invoke:

```sh
python3 tools/public_surface.py public-surface.json site/dist
```

Run it after the product's own docs build, for example:

```yaml
- run: npm run build
- run: python3 tools/public_surface.py public-surface.json site/dist
```

A minimal product-owned manifest is:

```json
{
  "base_url": "https://product.horonom.com/",
  "docs_url": "/docs/",
  "required_navigation": [
    "/docs/",
    "https://github.com/horonomy/product"
  ],
  "analytics": {
    "enabled": false
  }
}
```

The check is appropriate for company products with an established public
lifecycle (`beta`, `release_candidate`, or `available`) and for OSS projects
whose repository is the canonical project surface. It does not promote an
`experimental` or `not_yet_public` product; lifecycle and release evidence
remain governed by the Public Release Surface Contract.

The manifest accepts only `base_url`, `docs_url`, `required_navigation`, and
`analytics`. `base_url` is required. `docs_url` may be a same-host path such
as `/docs/` or an absolute HTTPS URL on a dedicated public docs host.
`required_navigation` is a list of exact URL targets that must appear as
anchors on `index.html`. `analytics.enabled` is always explicit; enabled
analytics also lists its `payload_fields`.

Every indexable HTML page must have exactly one self-referential same-site
canonical link. An explicit `noindex` error page may omit it. The artifact
must also have paired
`robots.txt`/`sitemap.xml` files, an exact sitemap declaration, valid sitemap
XML, and existing same-site page, asset, and fragment targets. External
public HTTPS navigation such as GitHub and support links remains valid.
Canonical and sitemap URLs stay within the declared site identity, including
the project prefix for GitHub Pages.

The checker scans generated text assets for common secret shapes, blocks
sensitive query keys, and rejects executable references to private IPs,
localhost, internal hostnames, Cloud Run origins, or non-HTTPS network URLs.
It permits explanatory local-first examples in page prose; links, fetches,
WebSockets, event streams, endpoint assignments, and CSS URLs remain checked.
Diagnostics name the file and invariant without printing the rejected URL,
query value, or secret.

Adoption is opt-in and product-owned. Pin the checker to an immutable commit
and verify its recorded SHA-256 before execution; do not download a mutable
branch during CI. A company product can use `<product>.horonom.com` plus
either `docs.<product>.horonom.com` or same-host `/docs/`; an OSS project can
use its GitHub Pages project path. Runtime/API hosts remain outside this
static artifact contract.

Run the product's normal docs/site build first, then its link, accessibility,
security, and baseline checks. After deployment, use a real browser and
external requests to verify responsive navigation, keyboard access, DNS,
TLS, redirects, canonical URLs, and the actual analytics requests. The
manifest and static heuristic do not prove that runtime analytics are private.

The first adoption set can reuse the same checker with product-owned
manifests: Libra (`https://libra.horonom.com/`, same-host `/docs/`), Eltanin
(`https://eltanin.horonom.com/`, same-host `/docs/`), and Glomeris
(`https://chisanan232.github.io/glomeris/`, whose Pages root is its docs
surface and whose current project link is
`https://github.com/Chisanan232/glomeris`). The company examples activate
only after their lifecycle and deployment gates authorize those domains.
Glomeris reuses its existing GitHub Pages build and project path.

## Deployment fragments

For an authorized company surface, reuse its existing Cloudflare Pages
project and account-scoped Pages token. Keep deployment disabled until the
lifecycle gate and DNS are approved. Run the product build and baseline before
the pinned deploy action:

```yaml
permissions:
  contents: read
jobs:
  deploy:
    if: ${{ vars.CLOUDFLARE_DEPLOY_ENABLED == 'true' }}
    runs-on: ubuntu-latest
    env:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - run: npm ci && npm run build
      - run: python3 tools/public_surface.py public-surface.json site/dist
      - uses: cloudflare/wrangler-action@953926a2e2182532811c01a25e53647d93bf07c0 # v4.1.3
        with:
          wranglerVersion: "4.130.0"
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          command: pages deploy site/dist --project-name=product-site --branch=main
```

For an OSS repository, keep GitHub Pages in that repository. Preserve the
repository's real build command, run the baseline on the assembled artifact,
and use Pages' least-privilege permissions and pinned actions:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      # Existing repository-owned build steps assemble the exact `site/` tree.
      - run: python3 tools/public_surface.py public-surface.json site
      - uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5
        with:
          path: site
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5
```

Both mechanisms have free tiers suitable for this baseline. Adoption does not
authorize a paid plan, a new runtime host, or a new Pages project solely for
portfolio symmetry.

## Adoption checklist

- [ ] Define `public-surface.json` beside the product's site build.
- [ ] Pin and checksum the shared checker; record the pin in the product repository.
- [ ] Set the canonical company or GitHub Pages URL actually deployed.
- [ ] List only product-owned navigation links and the intentional docs path/host.
- [ ] Declare analytics disabled or list every emitted payload field.
- [ ] Run the docs build, product link/accessibility/security checks, then this checker against the exact generated output in CI.
- [ ] Verify browser behavior and network requests against the deployed artifact.
- [ ] Capture external DNS, TLS, redirect, canonical, and link evidence after deployment.
- [ ] Keep deploy actions and the shared checker pinned to immutable commits.
- [ ] On rollback, restore the last verified artifact and keep the same gates enabled; fix the candidate before another rollout.
