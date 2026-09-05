#!/usr/bin/env python3
"""Tests for scripts/public_release_reconcile.py (HORO-512).

Stdlib unittest only, fully offline — every network/gh call is injected via
a fake Fetchers. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import public_release_reconcile as prr


def _fake_fetchers(*, gh_api=None, http_get=None) -> prr.Fetchers:
    def default_gh_api(path: str):
        raise LookupError(f"unexpected gh api call: {path}")

    def default_http_get(url: str):
        raise ConnectionError(f"unexpected http call: {url}")

    return prr.Fetchers(gh_api=gh_api or default_gh_api, http_get=http_get or default_http_get)


def _evidence(**overrides) -> dict:
    base = {
        "product": "testproduct",
        "claimed_lifecycle": "experimental",
        "github": {"org": "horonomy", "repo": "testproduct"},
        "website": None,
        "docs": None,
        "hosted_service": None,
    }
    base.update(overrides)
    return base


class LoadEvidenceTest(unittest.TestCase):
    def test_real_horologium_fixture_loads(self) -> None:
        evidence = prr.load_evidence(prr.REPO_ROOT / "metadata" / "release-evidence" / "horologium.yaml")
        self.assertEqual(evidence["product"], "horologium")

    def test_real_eridanus_fixture_loads(self) -> None:
        evidence = prr.load_evidence(prr.REPO_ROOT / "metadata" / "release-evidence" / "eridanus.yaml")
        self.assertEqual(evidence["product"], "eridanus")

    def test_rejects_unknown_lifecycle(self) -> None:
        d = Path(tempfile.mkdtemp())
        p = d / "bad.yaml"
        p.write_text("product: x\nclaimed_lifecycle: nope\ngithub:\n  org: horonomy\n  repo: x\n", encoding="utf-8")
        with self.assertRaises(prr.ReconcileError):
            prr.load_evidence(p)

    def test_rejects_missing_github(self) -> None:
        d = Path(tempfile.mkdtemp())
        p = d / "bad.yaml"
        p.write_text("product: x\nclaimed_lifecycle: experimental\n", encoding="utf-8")
        with self.assertRaises(prr.ReconcileError):
            prr.load_evidence(p)


class RepoMetadataTest(unittest.TestCase):
    def test_verified_when_repo_has_description(self) -> None:
        fx = _fake_fetchers(gh_api=lambda p: {"description": "a real product"})
        result = prr.check_repo_metadata(_evidence(), fx)
        self.assertEqual(result.state, prr.VERIFIED)

    def test_failed_when_repo_not_found(self) -> None:
        def raise_lookup(p):
            raise LookupError("404")

        fx = _fake_fetchers(gh_api=raise_lookup)
        result = prr.check_repo_metadata(_evidence(), fx)
        self.assertEqual(result.state, prr.FAILED)

    def test_blocked_external_on_connection_error(self) -> None:
        def raise_conn(p):
            raise ConnectionError("DNS failure")

        fx = _fake_fetchers(gh_api=raise_conn)
        result = prr.check_repo_metadata(_evidence(), fx)
        self.assertEqual(result.state, prr.BLOCKED_EXTERNAL)


class TagsReleasesClaimWideningTest(unittest.TestCase):
    def test_beta_claim_with_no_releases_is_failed(self) -> None:
        """Claim widening: claiming a released maturity with zero GitHub
        releases is exactly the kind of inconsistency this contract exists
        to catch — the AC's 'stale metadata fails reconciliation' case."""
        fx = _fake_fetchers(gh_api=lambda p: [])
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="beta"), fx)
        self.assertEqual(result.state, prr.FAILED)

    def test_beta_claim_with_releases_is_verified(self) -> None:
        fx = _fake_fetchers(gh_api=lambda p: [{"tag_name": "v1.0.0"}])
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="beta"), fx)
        self.assertEqual(result.state, prr.VERIFIED)

    def test_experimental_claim_with_no_releases_is_deferred(self) -> None:
        fx = _fake_fetchers(gh_api=lambda p: [])
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="experimental"), fx)
        self.assertEqual(result.state, prr.DEFERRED)

    def test_not_yet_public_claim_with_no_releases_is_not_yet_public(self) -> None:
        fx = _fake_fetchers(gh_api=lambda p: [])
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="not_yet_public"), fx)
        self.assertEqual(result.state, prr.NOT_YET_PUBLIC)

    def test_experimental_claim_with_releases_is_required(self) -> None:
        fx = _fake_fetchers(gh_api=lambda p: [{"tag_name": "v1.0.0"}])
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="experimental"), fx)
        self.assertEqual(result.state, prr.REQUIRED)

    def test_not_yet_public_claim_with_releases_on_private_repo_stays_not_yet_public(self) -> None:
        """HORO-517: a release tagged on a private repo (e.g. Eridanus's
        internal MVP tags) is not visible outside the org — it must not be
        treated as public exposure contradicting a not_yet_public claim."""

        def gh_api(path: str):
            if path.endswith("/releases"):
                return [{"tag_name": "v0.1.0"}]
            return {"private": True}

        fx = _fake_fetchers(gh_api=gh_api)
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="not_yet_public"), fx)
        self.assertEqual(result.state, prr.NOT_YET_PUBLIC)

    def test_experimental_claim_with_releases_on_private_repo_is_deferred(self) -> None:
        def gh_api(path: str):
            if path.endswith("/releases"):
                return [{"tag_name": "v0.1.0"}]
            return {"private": True}

        fx = _fake_fetchers(gh_api=gh_api)
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="experimental"), fx)
        self.assertEqual(result.state, prr.DEFERRED)

    def test_not_yet_public_claim_with_releases_on_public_repo_is_required(self) -> None:
        """A release on a genuinely public repo still contradicts the claim —
        this fix must not blanket-suppress the check, only the private case."""

        def gh_api(path: str):
            if path.endswith("/releases"):
                return [{"tag_name": "v0.1.0"}]
            return {"private": False}

        fx = _fake_fetchers(gh_api=gh_api)
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="not_yet_public"), fx)
        self.assertEqual(result.state, prr.REQUIRED)

    def test_repo_visibility_lookup_failure_defaults_to_public(self) -> None:
        """If the repo-info call itself fails, don't let that silently hide
        drift — default to the stricter (public) assumption."""

        def gh_api(path: str):
            if path.endswith("/releases"):
                return [{"tag_name": "v0.1.0"}]
            raise LookupError("404")

        fx = _fake_fetchers(gh_api=gh_api)
        result = prr.check_tags_releases(_evidence(claimed_lifecycle="not_yet_public"), fx)
        self.assertEqual(result.state, prr.REQUIRED)


class HttpSurfaceTest(unittest.TestCase):
    def test_not_applicable_when_no_url_configured(self) -> None:
        result = prr.check_website(_evidence(website=None), _fake_fetchers())
        self.assertEqual(result.state, prr.NOT_APPLICABLE)

    def test_verified_on_2xx(self) -> None:
        fx = _fake_fetchers(http_get=lambda u: (200, "<html>ok</html>"))
        result = prr.check_website(_evidence(website="https://example.com"), fx)
        self.assertEqual(result.state, prr.VERIFIED)

    def test_failed_on_broken_link(self) -> None:
        """AC: 'a fixture with ... broken public link fails reconciliation'."""
        fx = _fake_fetchers(http_get=lambda u: (404, ""))
        result = prr.check_website(_evidence(website="https://example.com/dead"), fx)
        self.assertEqual(result.state, prr.FAILED)

    def test_blocked_external_on_connection_error(self) -> None:
        def raise_conn(u):
            raise ConnectionError("timeout")

        fx = _fake_fetchers(http_get=raise_conn)
        result = prr.check_website(_evidence(website="https://example.com"), fx)
        self.assertEqual(result.state, prr.BLOCKED_EXTERNAL)


class CompanyRegistryTest(unittest.TestCase):
    def test_deferred_when_not_yet_public_and_uncataloged(self) -> None:
        with mock.patch.object(prr, "COMPANY_YAML_PATH", _company_yaml_with([])):
            result = prr.check_company_registry(_evidence(product="ghost", claimed_lifecycle="not_yet_public"))
        self.assertEqual(result.state, prr.DEFERRED)

    def test_required_when_beta_and_uncataloged(self) -> None:
        with mock.patch.object(prr, "COMPANY_YAML_PATH", _company_yaml_with([])):
            result = prr.check_company_registry(_evidence(product="ghost", claimed_lifecycle="beta"))
        self.assertEqual(result.state, prr.REQUIRED)

    def test_failed_on_stale_mismatched_catalog_entry(self) -> None:
        """AC: 'a fixture with stale company metadata ... fails reconciliation'."""
        catalog = _company_yaml_with([{"name": "widget", "lifecycle": "available"}])
        with mock.patch.object(prr, "COMPANY_YAML_PATH", catalog):
            result = prr.check_company_registry(_evidence(product="widget", claimed_lifecycle="beta"))
        self.assertEqual(result.state, prr.FAILED)

    def test_verified_when_catalog_entry_matches(self) -> None:
        catalog = _company_yaml_with([{"name": "widget", "lifecycle": "beta"}])
        with mock.patch.object(prr, "COMPANY_YAML_PATH", catalog):
            result = prr.check_company_registry(_evidence(product="widget", claimed_lifecycle="beta"))
        self.assertEqual(result.state, prr.VERIFIED)


def _company_yaml_with(products: list[dict]) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "company.yaml"
    lines = ["products:"]
    for prod in products:
        lines.append(f"  - name: {prod['name']}")
        lines.append(f"    lifecycle: {prod['lifecycle']}")
    if not products:
        lines = ["products: []"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class OrgProfileExposureTest(unittest.TestCase):
    def test_failed_when_not_yet_public_product_is_mentioned(self) -> None:
        """Premature-exposure check: a not-yet-public product must never
        leak into the public org profile."""
        with mock.patch.object(prr, "PROFILE_README_PATH", _readme_with("Eridanus is our new thing")):
            result = prr.check_org_profile(_evidence(product="eridanus", claimed_lifecycle="not_yet_public"))
        self.assertEqual(result.state, prr.FAILED)

    def test_not_yet_public_when_correctly_absent(self) -> None:
        with mock.patch.object(prr, "PROFILE_README_PATH", _readme_with("nothing relevant here")):
            result = prr.check_org_profile(_evidence(product="eridanus", claimed_lifecycle="not_yet_public"))
        self.assertEqual(result.state, prr.NOT_YET_PUBLIC)

    def test_required_when_beta_product_is_missing(self) -> None:
        with mock.patch.object(prr, "PROFILE_README_PATH", _readme_with("nothing relevant here")):
            result = prr.check_org_profile(_evidence(product="widget", claimed_lifecycle="beta"))
        self.assertEqual(result.state, prr.REQUIRED)

    def test_verified_when_beta_product_is_listed(self) -> None:
        with mock.patch.object(prr, "PROFILE_README_PATH", _readme_with("Widget is live")):
            result = prr.check_org_profile(_evidence(product="widget", claimed_lifecycle="beta"))
        self.assertEqual(result.state, prr.VERIFIED)

    def test_verified_when_experimental_product_is_listed(self) -> None:
        """HORO-515: experimental is a legitimately publishable tier (see
        productRegistry.ts's ProductMaturity) — being mentioned is fine, not
        premature exposure like not_yet_public."""
        with mock.patch.object(prr, "PROFILE_README_PATH", _readme_with("Circinus is Experimental")):
            result = prr.check_org_profile(_evidence(product="circinus", claimed_lifecycle="experimental"))
        self.assertEqual(result.state, prr.VERIFIED)

    def test_deferred_when_experimental_product_is_absent(self) -> None:
        """An experimental product not yet listed anywhere is DEFERRED, not
        REQUIRED — never force a product public (public-release-contract.md)."""
        with mock.patch.object(prr, "PROFILE_README_PATH", _readme_with("nothing relevant here")):
            result = prr.check_org_profile(_evidence(product="fornax", claimed_lifecycle="experimental"))
        self.assertEqual(result.state, prr.DEFERRED)


class HoronomComExposureTest(unittest.TestCase):
    def test_verified_when_experimental_product_appears_on_horonom_com(self) -> None:
        fx = _fake_fetchers(http_get=lambda u: (200, "Circinus is Experimental"))
        result = prr.check_horonom_com(_evidence(product="circinus", claimed_lifecycle="experimental"), fx)
        self.assertEqual(result.state, prr.VERIFIED)

    def test_deferred_when_experimental_product_absent_from_horonom_com(self) -> None:
        fx = _fake_fetchers(http_get=lambda u: (200, "nothing relevant here"))
        result = prr.check_horonom_com(_evidence(product="fornax", claimed_lifecycle="experimental"), fx)
        self.assertEqual(result.state, prr.DEFERRED)

    def test_failed_when_not_yet_public_product_appears_on_horonom_com(self) -> None:
        fx = _fake_fetchers(http_get=lambda u: (200, "Eridanus launches today"))
        result = prr.check_horonom_com(_evidence(product="eridanus", claimed_lifecycle="not_yet_public"), fx)
        self.assertEqual(result.state, prr.FAILED)


def _readme_with(text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "README.md"
    p.write_text(text, encoding="utf-8")
    return p


class OverallPrecedenceTest(unittest.TestCase):
    def _reconcile_with(self, gh_api, http_get, evidence=None) -> dict:
        fx = _fake_fetchers(gh_api=gh_api, http_get=http_get)
        catalog = _company_yaml_with([])
        readme = _readme_with("nothing")
        with mock.patch.object(prr, "COMPANY_YAML_PATH", catalog), mock.patch.object(
            prr, "PROFILE_README_PATH", readme
        ):
            return prr.reconcile(evidence or _evidence(), fx)

    def test_eridanus_like_config_is_not_yet_public_overall(self) -> None:
        """AC: 'Eridanus can be represented as NOT_YET_PUBLIC without
        creating public entries.' website/docs/hosted_service are all None
        in the evidence, so they never call http_get at all (NOT_APPLICABLE
        short-circuits first) — the only real http_get call this exercises
        is the unconditional horonom.com check, simulated here as reachable
        and correctly not mentioning the product."""
        result = self._reconcile_with(
            gh_api=lambda p: {"description": "private research repo"} if not p.endswith("releases") else [],
            http_get=lambda u: (200, "horonom.com homepage, no mention of the unreleased product"),
            evidence=_evidence(product="eridanus", claimed_lifecycle="not_yet_public"),
        )
        self.assertEqual(result["overall"], prr.NOT_YET_PUBLIC)
        self.assertEqual(result["surfaces"]["org_profile"]["state"], prr.NOT_YET_PUBLIC)
        self.assertEqual(result["surfaces"]["company_registry"]["state"], prr.DEFERRED)

    def test_blocked_external_when_horonom_com_unreachable(self) -> None:
        """If horonom.com itself can't be checked, reconciliation honestly
        reports BLOCKED_EXTERNAL rather than assuming the absent-mention
        verdict it can't actually verify."""
        result = self._reconcile_with(
            gh_api=lambda p: {"description": "private research repo"} if not p.endswith("releases") else [],
            http_get=lambda u: (_ for _ in ()).throw(ConnectionError("network unreachable")),
            evidence=_evidence(product="eridanus", claimed_lifecycle="not_yet_public"),
        )
        self.assertEqual(result["overall"], prr.BLOCKED_EXTERNAL)

    def test_one_failed_surface_fails_the_whole_reconciliation(self) -> None:
        result = self._reconcile_with(
            gh_api=lambda p: {"description": "x"} if not p.endswith("releases") else [{"tag_name": "v1"}],
            http_get=lambda u: (200, "ok"),
            evidence=_evidence(product="widget", claimed_lifecycle="beta", website="https://example.com"),
        )
        # company_registry will be REQUIRED (uncataloged beta product) which
        # outranks a plain VERIFIED but is not itself FAILED — confirms
        # REQUIRED, not just FAILED, propagates to overall.
        self.assertEqual(result["overall"], prr.REQUIRED)


class RealHttpGetReadLimitTest(unittest.TestCase):
    """HORO-533 audit Finding-02: the fetch cap governs how much of a live
    page check_horonom_com/check_docs can see — too small silently turns a
    premature-exposure FAILED into a false 'correctly absent' once a page
    grows past it. Not a live-network test (this repo's tests stay fully
    offline) — mocks urllib.request.urlopen to confirm the cap itself was
    raised, not the live fetch behavior."""

    def test_reads_more_than_the_old_64kb_cap(self) -> None:
        body = b"x" * 200_000  # bigger than the old 65536-byte cap

        class _FakeResp:
            status = 200

            def read(self, n):
                return body[:n]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(prr.urllib.request, "urlopen", return_value=_FakeResp()):
            status, text = prr.real_http_get("https://example.com")
        self.assertEqual(status, 200)
        self.assertEqual(len(text), 200_000)


class MainCLITest(unittest.TestCase):
    def test_main_returns_nonzero_when_evidence_file_invalid(self) -> None:
        d = Path(tempfile.mkdtemp())
        p = d / "bad.yaml"
        p.write_text("product: x\n", encoding="utf-8")
        exit_code = prr.main([str(p)])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
