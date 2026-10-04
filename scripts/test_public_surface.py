import json
import tempfile
import unittest
from pathlib import Path

try:
    from public_surface import validate
except ModuleNotFoundError:  # direct `python -m unittest scripts/...`
    from scripts.public_surface import validate


class PublicSurfaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def manifest(self, **extra):
        value = {"base_url": "https://example.horonom.com", "required_navigation": ["/docs"]}
        value.update(extra)
        return value

    def test_accepts_company_surface_and_same_host_docs(self):
        self.write("index.html", '<link rel="canonical" href="https://example.horonom.com/"><a href="/docs">Docs</a>')
        self.write("robots.txt", "User-agent: *\nSitemap: https://example.horonom.com/sitemap.xml\n")
        self.write("sitemap.xml", "<urlset><url><loc>https://example.horonom.com/</loc></url></urlset>")
        self.assertEqual(validate(self.manifest(docs_url="/docs"), self.root), [])

    def test_fails_private_origin_secret_and_encoded_traversal(self):
        self.write("index.html", '<link rel="canonical" href="https://example.horonom.com/"><a href="https://localhost:8080/%2e%2e/admin?token=abc">/</a>\nAKIA1234567890ABCDEF')
        findings = validate(self.manifest(), self.root)
        rules = {item.rule for item in findings}
        self.assertTrue({"private-origin", "secret-scan", "path-traversal", "query-leakage"} <= rules)

    def test_fails_cross_host_sitemap_and_missing_navigation(self):
        self.write("index.html", '<link rel="canonical" href="https://example.horonom.com/">')
        self.write("robots.txt", "Sitemap: https://example.horonom.com/sitemap.xml")
        self.write("sitemap.xml", "<urlset><url><loc>https://evil.example/x</loc></url></urlset>")
        rules = {item.rule for item in validate(self.manifest(), self.root)}
        self.assertIn("robots-sitemap", rules)
        self.assertIn("navigation", rules)

    def test_fails_analytics_sensitive_fields(self):
        self.write("index.html", '<link rel="canonical" href="https://example.horonom.com/"><a href="/docs">Docs</a>')
        findings = validate(self.manifest(analytics={"enabled": True, "payload_fields": ["route", "email"]}), self.root)
        self.assertTrue(any(item.rule == "privacy-analytics" for item in findings))
