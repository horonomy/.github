#!/usr/bin/env python3
"""Horonom Public Release Surface Contract reconciler (HORO-512).

Reconciles a product's public presence across every surface that could
diverge from it: GitHub repo/tags/releases, website/docs/hosted-service
reachability and TLS, `metadata/company.yaml`'s catalog entry,
`profile/README.md`, and `horonom.com` itself — never trusting one
surface's claim (a Jira ticket's status, a hand-typed "VERIFIED") over
what the others actually show. See governance/releases/public-release-contract.md
for the full state machine and per-surface rules this implements.

Every live check is injected (`Fetchers`) so this module is fully testable
offline; the CLI wires real `urllib`/`gh api` fetchers.

Usage:
    python3 scripts/public_release_reconcile.py metadata/release-evidence/horologium.yaml
    python3 scripts/public_release_reconcile.py metadata/release-evidence/horologium.yaml --json
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import generate_company_metadata as company_meta

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANY_YAML_PATH = REPO_ROOT / "metadata" / "company.yaml"
PROFILE_README_PATH = REPO_ROOT / "profile" / "README.md"
HORONOM_COM_URL = "https://horonom.com"

# States, most-restrictive first — see governance/releases/public-release-contract.md.
VERIFIED = "VERIFIED"
REQUIRED = "REQUIRED"
DEFERRED = "DEFERRED"
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_YET_PUBLIC = "NOT_YET_PUBLIC"
BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
FAILED = "FAILED"

_PRECEDENCE = [FAILED, BLOCKED_EXTERNAL, REQUIRED, NOT_YET_PUBLIC, DEFERRED]

_LIFECYCLES_IMPLYING_RELEASE = frozenset({"beta", "release_candidate", "available"})
_LIFECYCLES_NOT_YET_PUBLIC = frozenset({"experimental", "not_yet_public"})


class ReconcileError(RuntimeError):
    pass


@dataclass
class SurfaceResult:
    state: str
    detail: str


@dataclass
class Fetchers:
    """Injectable I/O boundary — the CLI wires real network/gh calls; tests
    inject fakes so every derivation rule is verifiable offline."""

    gh_api: Callable[[str], Any]
    http_get: Callable[[str], tuple[int, str]]  # returns (status, body) or raises


def real_gh_api(path: str) -> Any:
    try:
        proc = subprocess.run(
            ["gh", "api", path], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectionError(f"gh api unavailable: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.lower()
        if "could not resolve" in stderr or "timeout" in stderr or "rate limit" in stderr:
            raise ConnectionError(proc.stderr.strip())
        raise LookupError(proc.stderr.strip())
    return json.loads(proc.stdout)


def real_http_get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "horonom-release-reconcile/1"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as resp:
            return resp.status, resp.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError) as exc:
        raise ConnectionError(str(exc)) from exc


REAL_FETCHERS = Fetchers(gh_api=real_gh_api, http_get=real_http_get)


# ---------------------------------------------------------------------------
# Evidence config
# ---------------------------------------------------------------------------
def load_evidence(path: Path) -> dict[str, Any]:
    try:
        data = company_meta.parse_yaml(path.read_text(encoding="utf-8"))
    except company_meta.YamlError as exc:
        raise ReconcileError(f"{path}: {exc}") from exc
    for field_name in ("product", "claimed_lifecycle", "github"):
        if field_name not in data:
            raise ReconcileError(f"{path}: missing required field {field_name!r}")
    lifecycle = data["claimed_lifecycle"]
    if lifecycle not in (_LIFECYCLES_IMPLYING_RELEASE | _LIFECYCLES_NOT_YET_PUBLIC):
        raise ReconcileError(f"{path}: unknown claimed_lifecycle {lifecycle!r}")
    github = data["github"]
    if not isinstance(github, dict) or not github.get("org") or not github.get("repo"):
        raise ReconcileError(f"{path}: 'github' must have 'org' and 'repo'")
    return data


def _url_or_none(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value in (None, "", "null"):
        return None
    return value


# ---------------------------------------------------------------------------
# Per-surface checks
# ---------------------------------------------------------------------------
def check_repo_metadata(evidence: dict[str, Any], fx: Fetchers) -> SurfaceResult:
    org, repo = evidence["github"]["org"], evidence["github"]["repo"]
    try:
        info = fx.gh_api(f"repos/{org}/{repo}")
    except ConnectionError as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"gh api unreachable: {exc}")
    except LookupError as exc:
        return SurfaceResult(FAILED, f"repo {org}/{repo} not found: {exc}")
    if not info.get("description"):
        return SurfaceResult(REQUIRED, f"repo {org}/{repo} has no description")
    return SurfaceResult(VERIFIED, f"repo {org}/{repo} exists, description present")


def check_tags_releases(evidence: dict[str, Any], fx: Fetchers) -> SurfaceResult:
    org, repo = evidence["github"]["org"], evidence["github"]["repo"]
    lifecycle = evidence["claimed_lifecycle"]
    try:
        releases = fx.gh_api(f"repos/{org}/{repo}/releases")
    except ConnectionError as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"gh api unreachable: {exc}")
    except LookupError as exc:
        return SurfaceResult(FAILED, f"could not list releases: {exc}")
    has_release = bool(releases)
    if lifecycle in _LIFECYCLES_IMPLYING_RELEASE:
        if has_release:
            return SurfaceResult(VERIFIED, f"{len(releases)} release(s) found, matches claimed lifecycle {lifecycle!r}")
        return SurfaceResult(
            FAILED,
            f"claimed_lifecycle={lifecycle!r} implies a release exists, but repo has none — claim wider than evidence",
        )
    # experimental / not_yet_public: zero releases is the correct, expected state
    # UNLESS the repo is private — a release on a private repo is not visible to
    # anyone outside the org, so it is not public exposure and does not contradict
    # the claim (see HORO-517: this exact gap surfaced against the real Eridanus
    # repo, which ships internal MVP tags while staying not_yet_public). Default
    # to "public" (the stricter, exposure-surfacing behavior) whenever visibility
    # can't be determined — never let an unknown default silently hide drift.
    if has_release:
        is_private = False
        try:
            repo_info = fx.gh_api(f"repos/{org}/{repo}")
            is_private = bool(repo_info.get("private"))
        except (ConnectionError, LookupError, AttributeError):
            is_private = False
        if is_private:
            return SurfaceResult(
                NOT_YET_PUBLIC if lifecycle == "not_yet_public" else DEFERRED,
                f"{len(releases)} release(s) exist but repo is private — not public exposure, consistent with claimed lifecycle {lifecycle!r}",
            )
        return SurfaceResult(
            REQUIRED,
            f"claimed_lifecycle={lifecycle!r} but {len(releases)} release(s) already exist on a public repo — reconcile the claimed lifecycle",
        )
    return SurfaceResult(NOT_YET_PUBLIC if lifecycle == "not_yet_public" else DEFERRED, "no releases yet, consistent with claimed lifecycle")


def _check_http_surface(url: str | None, fx: Fetchers, label: str) -> SurfaceResult:
    if url is None:
        return SurfaceResult(NOT_APPLICABLE, f"no {label} configured")
    try:
        status, _ = fx.http_get(url)
    except ConnectionError as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"{label} unreachable: {exc}")
    if 200 <= status < 400:
        return SurfaceResult(VERIFIED, f"{label} responded {status}")
    return SurfaceResult(FAILED, f"{label} responded {status}")


def check_website(evidence: dict[str, Any], fx: Fetchers) -> SurfaceResult:
    return _check_http_surface(_url_or_none(evidence, "website"), fx, "website")


def check_docs(evidence: dict[str, Any], fx: Fetchers) -> SurfaceResult:
    return _check_http_surface(_url_or_none(evidence, "docs"), fx, "docs")


def check_hosted_service(evidence: dict[str, Any], fx: Fetchers) -> SurfaceResult:
    return _check_http_surface(_url_or_none(evidence, "hosted_service"), fx, "hosted_service")


def check_domain_tls(evidence: dict[str, Any], website_result: SurfaceResult, docs_result: SurfaceResult, hosted_result: SurfaceResult) -> SurfaceResult:
    # Piggybacks on the HTTP checks above: a successful HTTPS fetch already
    # proved a valid TLS handshake (urllib raises ssl.SSLError otherwise,
    # surfaced as BLOCKED_EXTERNAL by _check_http_surface). Nothing to
    # re-check independently; this just reports the worst of the three.
    results = [r for r in (website_result, docs_result, hosted_result) if r.state != NOT_APPLICABLE]
    if not results:
        return SurfaceResult(NOT_APPLICABLE, "no HTTPS surface configured")
    worst = min(results, key=lambda r: _rank(r.state))
    if worst.state == VERIFIED:
        return SurfaceResult(VERIFIED, "TLS handshake succeeded on all configured HTTPS surfaces")
    return SurfaceResult(worst.state, f"inherited from {worst.detail}")


def check_company_registry(evidence: dict[str, Any]) -> SurfaceResult:
    product = evidence["product"]
    lifecycle = evidence["claimed_lifecycle"]
    try:
        catalog = company_meta.parse_yaml(COMPANY_YAML_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, company_meta.YamlError) as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"could not read metadata/company.yaml: {exc}")
    products = catalog.get("products") or []
    entry = next((p for p in products if isinstance(p, dict) and p.get("name", "").lower() == product.lower()), None)
    if entry is None:
        if lifecycle in _LIFECYCLES_NOT_YET_PUBLIC:
            return SurfaceResult(DEFERRED, "not yet cataloged, consistent with claimed lifecycle")
        return SurfaceResult(REQUIRED, f"claimed_lifecycle={lifecycle!r} but no metadata/company.yaml catalog entry exists")
    catalog_lifecycle = entry.get("lifecycle")
    if catalog_lifecycle != lifecycle:
        return SurfaceResult(
            FAILED,
            f"metadata/company.yaml lists lifecycle={catalog_lifecycle!r}, product claims {lifecycle!r} — stale/conflicting catalog entry",
        )
    return SurfaceResult(VERIFIED, "catalog entry present and matches claimed lifecycle")


def check_org_profile(evidence: dict[str, Any]) -> SurfaceResult:
    product = evidence["product"]
    lifecycle = evidence["claimed_lifecycle"]
    try:
        text = PROFILE_README_PATH.read_text(encoding="utf-8").lower()
    except FileNotFoundError as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"could not read profile/README.md: {exc}")
    mentioned = product.lower() in text
    if lifecycle in _LIFECYCLES_NOT_YET_PUBLIC:
        if mentioned:
            return SurfaceResult(FAILED, "product is mentioned in profile/README.md despite being not-yet-public — premature exposure")
        return SurfaceResult(NOT_YET_PUBLIC, "correctly absent from the public org profile")
    if mentioned:
        return SurfaceResult(VERIFIED, "product is listed in the public org profile")
    return SurfaceResult(REQUIRED, f"claimed_lifecycle={lifecycle!r} but product is not listed in profile/README.md")


def check_horonom_com(evidence: dict[str, Any], fx: Fetchers) -> SurfaceResult:
    product = evidence["product"]
    lifecycle = evidence["claimed_lifecycle"]
    try:
        _, body = fx.http_get(HORONOM_COM_URL)
    except ConnectionError as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"horonom.com unreachable: {exc}")
    mentioned = product.lower() in body.lower()
    if lifecycle in _LIFECYCLES_NOT_YET_PUBLIC:
        if mentioned:
            return SurfaceResult(FAILED, "product appears on horonom.com despite being not-yet-public — premature exposure")
        return SurfaceResult(NOT_YET_PUBLIC, "correctly absent from horonom.com")
    if mentioned:
        return SurfaceResult(VERIFIED, "product appears on horonom.com")
    return SurfaceResult(REQUIRED, f"claimed_lifecycle={lifecycle!r} but product does not appear on horonom.com")


def check_cross_links(evidence: dict[str, Any], website_result: SurfaceResult, docs_result: SurfaceResult, fx: Fetchers) -> SurfaceResult:
    website_url = _url_or_none(evidence, "website")
    docs_url = _url_or_none(evidence, "docs")
    if not website_url or not docs_url or website_result.state != VERIFIED or docs_result.state != VERIFIED:
        return SurfaceResult(NOT_APPLICABLE, "website and docs are not both configured and verified")
    try:
        _, body = fx.http_get(website_url)
    except ConnectionError as exc:
        return SurfaceResult(BLOCKED_EXTERNAL, f"website unreachable while checking cross-links: {exc}")
    docs_host = re.sub(r"^https?://", "", docs_url).split("/")[0]
    if docs_host in body:
        return SurfaceResult(VERIFIED, f"website links to docs host {docs_host}")
    return SurfaceResult(FAILED, f"website does not link to docs host {docs_host}")


def check_product_truth(repo_metadata_result: SurfaceResult) -> SurfaceResult:
    # Proxied by repo_metadata: the product's own repo is authoritative for
    # its capability claim (governance/releases/public-surfaces.md). This
    # contract does not attempt to semantically verify the claim's content.
    if repo_metadata_result.state == VERIFIED:
        return SurfaceResult(VERIFIED, "product repo reachable — authoritative source exists")
    return SurfaceResult(repo_metadata_result.state, f"inherited from repo_metadata: {repo_metadata_result.detail}")


def _rank(state: str) -> int:
    try:
        return _PRECEDENCE.index(state)
    except ValueError:
        return len(_PRECEDENCE)  # VERIFIED / NOT_APPLICABLE sort last (best)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
def reconcile(evidence: dict[str, Any], fx: Fetchers = REAL_FETCHERS) -> dict[str, Any]:
    repo_metadata = check_repo_metadata(evidence, fx)
    tags_releases = check_tags_releases(evidence, fx)
    website = check_website(evidence, fx)
    docs = check_docs(evidence, fx)
    hosted_service = check_hosted_service(evidence, fx)
    domain_tls = check_domain_tls(evidence, website, docs, hosted_service)
    company_registry = check_company_registry(evidence)
    org_profile = check_org_profile(evidence)
    horonom_com = check_horonom_com(evidence, fx)
    cross_links = check_cross_links(evidence, website, docs, fx)
    product_truth = check_product_truth(repo_metadata)

    surfaces = {
        "product_truth": product_truth,
        "repo_metadata": repo_metadata,
        "tags_releases": tags_releases,
        "website": website,
        "docs": docs,
        "hosted_service": hosted_service,
        "domain_tls": domain_tls,
        "company_registry": company_registry,
        "org_profile": org_profile,
        "horonom_com": horonom_com,
        "cross_links": cross_links,
    }

    non_applicable_states = [r.state for r in surfaces.values() if r.state not in (VERIFIED, NOT_APPLICABLE)]
    overall = VERIFIED
    for candidate in _PRECEDENCE:
        if candidate in non_applicable_states:
            overall = candidate
            break

    return {
        "product": evidence["product"],
        "claimed_lifecycle": evidence["claimed_lifecycle"],
        "overall": overall,
        "surfaces": {name: {"state": r.state, "detail": r.detail} for name, r in surfaces.items()},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("evidence_path", help="Path to a metadata/release-evidence/<product>.yaml file.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        evidence = load_evidence(Path(args.evidence_path))
        result = reconcile(evidence)
    except ReconcileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['product']} (claimed_lifecycle={result['claimed_lifecycle']}): overall={result['overall']}")
        for name, info in result["surfaces"].items():
            print(f"  {name}: {info['state']} — {info['detail']}")
    return 1 if result["overall"] == FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
