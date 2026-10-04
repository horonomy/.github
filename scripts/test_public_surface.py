import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import public_surface as surface
except ModuleNotFoundError:
    from scripts import public_surface as surface

validate = surface.validate


class PublicSurfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, body: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def manifest(self, **extra: object) -> dict[str, object]:
        value: dict[str, object] = {
            "base_url": "https://example.horonom.com/",
            "required_navigation": ["/docs/", "https://github.com/horonomy/example"],
            "analytics": {"enabled": False},
        }
        value.update(extra)
        return value

    def valid_site(self) -> None:
        self.write(
            "index.html",
            '<html><head><link href="https://example.horonom.com/" '
            'rel="alternate canonical"></head><body><a href="/docs/">Docs</a>'
            '<a href="https://github.com/horonomy/example">GitHub</a></body></html>',
        )
        self.write(
            "docs/index.html",
            '<link rel="canonical" href="https://example.horonom.com/docs/">'
            '<h1 id="start">Docs</h1>',
        )
        self.write(
            "robots.txt", "User-agent: *\nSitemap: https://example.horonom.com/sitemap.xml\n"
        )
        self.write(
            "sitemap.xml",
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.horonom.com/</loc></url>"
            "<url><loc>https://example.horonom.com/docs/</loc></url></urlset>",
        )

    def rules(self, manifest: dict[str, object] | None = None) -> set[str]:
        return {finding.rule for finding in validate(manifest or self.manifest(), self.root)}

    def test_accepts_company_site_external_navigation_and_dedicated_docs(self) -> None:
        self.valid_site()
        manifest = self.manifest(docs_url="https://docs.example.horonom.com/")
        self.assertEqual(validate(manifest, self.root), [])

    def test_accepts_github_pages_project_path(self) -> None:
        self.write(
            "index.html",
            '<link rel="canonical" href="https://chisanan232.github.io/glomeris/">'
            '<a href="/glomeris/docs/">Docs</a>',
        )
        self.write(
            "docs/index.html",
            '<link rel="canonical" href="https://chisanan232.github.io/glomeris/docs/">',
        )
        self.write("robots.txt", "Sitemap: https://chisanan232.github.io/glomeris/sitemap.xml\n")
        self.write(
            "sitemap.xml",
            "<urlset><url><loc>https://chisanan232.github.io/glomeris/</loc></url></urlset>",
        )
        manifest = {
            "base_url": "https://chisanan232.github.io/glomeris/",
            "docs_url": "/glomeris/docs/",
            "required_navigation": ["/glomeris/docs/"],
            "analytics": {"enabled": False},
        }
        self.assertEqual(validate(manifest, self.root), [])

    def test_rejects_invalid_base_and_docs_urls(self) -> None:
        self.valid_site()
        bad_base = self.manifest(base_url="https://user:secret@example.horonom.com/")
        self.assertIn("canonical-identity", self.rules(bad_base))
        bad_docs = self.manifest(docs_url="//docs.example.horonom.com/")
        self.assertIn("docs-link", self.rules(bad_docs))
        hostless_docs = self.manifest(docs_url="https:///reference/")
        self.assertIn("docs-link", self.rules(hostless_docs))
        internal_docs = self.manifest(docs_url="https://intranet/reference/")
        self.assertIn("docs-link", self.rules(internal_docs))

    def test_rejects_missing_same_site_docs_target(self) -> None:
        self.valid_site()
        self.assertIn("docs-link", self.rules(self.manifest(docs_url="/reference/")))

    def test_detects_secret_private_origin_and_encoded_traversal_without_echo(self) -> None:
        self.valid_site()
        self.write(
            "app.js", 'const origin="https://10.2.3.4/api"; const key="AKIA1234567890ABCDEF";'
        )
        self.write(
            "index.html",
            '<link rel="canonical" href="https://example.horonom.com/">'
            '<a href="/%252e%252e/admin?token=redacted">Bad</a>',
        )
        findings = validate(self.manifest(required_navigation=[]), self.root)
        self.assertTrue(
            {"private-origin", "secret-scan", "link-integrity"}.issubset(
                {item.rule for item in findings}
            )
        )
        self.assertNotIn("redacted", "\n".join(item.detail for item in findings))

    def test_rejects_encoded_traversal_before_url_join_can_normalize_it(self) -> None:
        self.valid_site()
        self.write(
            "docs/index.html", '<link rel="canonical" href="./"><a href="/%2e%2e/">Escape</a>'
        )
        self.assertIn("link-integrity", self.rules())

    def test_rejects_every_unsafe_executable_url_without_echoing_values(self) -> None:
        self.valid_site()
        self.write(
            "app.js",
            'fetch("https://user:password@api.example.com/data"); '
            'const api_url="https://api.example.com/data?token=private";',
        )
        findings = [
            item for item in validate(self.manifest(), self.root) if item.rule == "executable-url"
        ]
        self.assertEqual(len(findings), 2)
        detail = "\n".join(item.detail for item in findings)
        self.assertNotIn("password", detail)
        self.assertNotIn("private", detail)

    def test_does_not_treat_versions_or_public_ipv6_as_private_origin(self) -> None:
        self.valid_site()
        self.write(
            "app.js", 'const version="10.2.3.4"; const support="https://[2606:4700:4700::1111]/";'
        )
        self.assertNotIn("private-origin", self.rules())

    def test_allows_documented_local_example_but_rejects_executable_link(self) -> None:
        self.valid_site()
        self.write(
            "docs/index.html",
            '<link rel="canonical" href="https://example.horonom.com/docs/">'
            "<p>For a local-first setup, open https://localhost:8080 after "
            "starting the daemon.</p>",
        )
        self.assertNotIn("private-origin", self.rules())
        self.write(
            "docs/index.html",
            '<link rel="canonical" href="https://example.horonom.com/docs/">'
            '<a href="https://localhost:8080">Open daemon</a>',
        )
        self.assertIn("link-integrity", self.rules())

    def test_rejects_missing_internal_target_and_fragment(self) -> None:
        self.valid_site()
        self.write(
            "index.html",
            '<link rel="canonical" href="https://example.horonom.com/">'
            '<a href="/missing/">Missing</a><a href="/docs/#absent">Fragment</a>',
        )
        findings = [
            item
            for item in validate(self.manifest(required_navigation=[]), self.root)
            if item.rule == "link-integrity"
        ]
        self.assertEqual(len(findings), 2)

    def test_resolves_relative_links_from_the_containing_page(self) -> None:
        self.valid_site()
        self.write(
            "docs/index.html",
            '<link rel="canonical" href="./"><a href="next.html#details">Next</a>',
        )
        self.write(
            "docs/next.html",
            '<link rel="canonical" href="./next.html"><h2 id="details">Details</h2>',
        )
        self.assertEqual(validate(self.manifest(), self.root), [])

    def test_allows_mdbook_parent_link_inside_github_pages_project(self) -> None:
        self.write(
            "index.html",
            '<link rel="canonical" href="https://chisanan232.github.io/glomeris/">'
            '<a href="guide/install/">Install</a>',
        )
        self.write("guide/index.html", '<link rel="canonical" href="./">')
        self.write(
            "guide/install/index.html",
            '<link rel="canonical" href="./"><a href="../">Guide</a>',
        )
        self.write("robots.txt", "Sitemap: https://chisanan232.github.io/glomeris/sitemap.xml\n")
        self.write(
            "sitemap.xml",
            "<urlset><url><loc>https://chisanan232.github.io/glomeris/</loc></url></urlset>",
        )
        manifest = {
            "base_url": "https://chisanan232.github.io/glomeris/",
            "docs_url": "https://chisanan232.github.io/glomeris/",
            "required_navigation": ["guide/install/"],
            "analytics": {"enabled": False},
        }
        self.assertEqual(validate(manifest, self.root), [])

    def test_rejects_parent_link_that_escapes_github_pages_project(self) -> None:
        self.write(
            "index.html",
            '<link rel="canonical" href="https://chisanan232.github.io/glomeris/">'
            '<a href="../../outside/">Escape</a>',
        )
        self.write("robots.txt", "Sitemap: https://chisanan232.github.io/glomeris/sitemap.xml\n")
        self.write(
            "sitemap.xml",
            "<urlset><url><loc>https://chisanan232.github.io/glomeris/</loc></url></urlset>",
        )
        manifest = {
            "base_url": "https://chisanan232.github.io/glomeris/",
            "required_navigation": [],
            "analytics": {"enabled": False},
        }
        self.assertIn("link-integrity", self.rules(manifest))

    def test_allows_data_image_asset_but_rejects_data_navigation(self) -> None:
        self.valid_site()
        self.write(
            "docs/index.html",
            '<link rel="canonical" href="./">'
            '<img src="data:image/png;base64,AA=="><a href="data:text/html,unsafe">Open</a>',
        )
        self.assertIn("link-integrity", self.rules())

    def test_requires_page_canonical_to_match_its_public_url(self) -> None:
        self.valid_site()
        self.write("docs/index.html", '<link rel="canonical" href="https://example.horonom.com/">')
        self.assertIn("canonical-identity", self.rules())

    def test_allows_noindex_error_page_without_canonical(self) -> None:
        self.valid_site()
        self.write("404.html", '<meta name="robots" content="nofollow, noindex"><h1>Not found</h1>')
        self.assertEqual(validate(self.manifest(), self.root), [])

    def test_required_navigation_must_be_an_exact_anchor(self) -> None:
        self.valid_site()
        self.assertIn("navigation", self.rules(self.manifest(required_navigation=["/docs/#start"])))

    def test_rejects_malformed_sitemap_xml(self) -> None:
        self.valid_site()
        self.write("sitemap.xml", "<urlset><url><loc>https://example.horonom.com/</loc></url>")
        self.assertIn("robots-sitemap", self.rules())

    def test_rejects_entity_expansion_before_xml_parser_runs(self) -> None:
        self.valid_site()
        attack = (
            '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol1 "&lol;&lol;">]>'
            "<urlset><url><loc>&lol1;</loc></url></urlset>"
        )
        self.write("sitemap.xml", attack)
        with mock.patch.object(surface.ET, "fromstring") as parser:
            self.assertIn("robots-sitemap", self.rules())
        parser.assert_not_called()

    def test_rejects_external_entity_before_xml_parser_runs(self) -> None:
        self.valid_site()
        attack = (
            '<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<urlset><url><loc>&xxe;</loc></url></urlset>"
        )
        self.write("sitemap.xml", attack)
        with mock.patch.object(surface.ET, "fromstring") as parser:
            self.assertIn("robots-sitemap", self.rules())
        parser.assert_not_called()

    def test_rejects_sitemap_over_bounded_parse_limit(self) -> None:
        self.valid_site()
        self.write("sitemap.xml", " " * 1_000_001)
        self.assertIn("robots-sitemap", self.rules())

    def test_rejects_foreign_sitemap_and_wrong_robots_target(self) -> None:
        self.valid_site()
        self.write("robots.txt", "Sitemap: https://example.horonom.com/other.xml\n")
        self.write("sitemap.xml", "<urlset><url><loc>https://evil.example/</loc></url></urlset>")
        self.assertIn("robots-sitemap", self.rules())

    def test_rejects_sitemap_page_absent_from_artifact(self) -> None:
        self.valid_site()
        self.write(
            "sitemap.xml",
            "<urlset><url><loc>https://example.horonom.com/absent/</loc></url></urlset>",
        )
        self.assertIn("robots-sitemap", self.rules())

    def test_rejects_unsafe_analytics_declaration_and_artifact_assignment(self) -> None:
        self.valid_site()
        self.write("analytics.js", "send({tenant_id: value})")
        manifest = self.manifest(
            analytics={"enabled": True, "payload_fields": ["route", "repository_name"]}
        )
        findings = [
            item for item in validate(manifest, self.root) if item.rule == "privacy-analytics"
        ]
        self.assertEqual(len(findings), 2)

    def test_rejects_unknown_analytics_scope(self) -> None:
        self.valid_site()
        manifest = self.manifest(analytics={"enabled": False, "collect_dom": True})
        self.assertIn("privacy-analytics", self.rules(manifest))

    def test_requires_explicit_analytics_disposition_and_known_manifest_fields(self) -> None:
        self.valid_site()
        manifest = self.manifest()
        del manifest["analytics"]
        manifest["copied_product_truth"] = True
        rules = self.rules(manifest)
        self.assertIn("manifest", rules)
        self.assertIn("privacy-analytics", rules)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_does_not_read_symlink_that_escapes_artifact_root(self) -> None:
        self.valid_site()
        outside = Path(self.tmp.name).parent / f"{Path(self.tmp.name).name}-outside.js"
        outside.write_text('send({email: "private@example.com"})', encoding="utf-8")
        try:
            (self.root / "escaped.js").symlink_to(outside)
            findings = validate(
                self.manifest(analytics={"enabled": True, "payload_fields": ["route"]}), self.root
            )
            self.assertIn("artifact-boundary", {item.rule for item in findings})
            self.assertNotIn("privacy-analytics", {item.rule for item in findings})
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
