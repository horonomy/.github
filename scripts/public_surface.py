#!/usr/bin/env python3
"""Offline invariants for generated public web artifacts.

The checker deliberately validates boundaries and discoverability, not product
copy or design.  A manifest keeps product-specific paths and navigation in the
product repository while this module supplies the shared safety contract.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse


SECRET = re.compile(r"(?:ghp_[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
PRIVATE_ORIGIN = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\.internal(?:[:/]|$)|run\.app(?:[:/]|$))", re.I)
SENSITIVE_QUERY = re.compile(r"(?:token|secret|password|api[_-]?key|access[_-]?token|email)=", re.I)
ATTR_URL = re.compile(r"(?:href|src)\s*=\s*[\"']([^\"']+)", re.I)


@dataclass(frozen=True)
class Finding:
    rule: str
    detail: str


def _url(value: str, base: str) -> str:
    return urljoin(base.rstrip("/") + "/", value)


def validate(manifest: dict, root: Path) -> list[Finding]:
    """Return deterministic findings; an empty list means the artifact passes."""
    findings: list[Finding] = []
    base = manifest.get("base_url", "")
    if not base.startswith("https://"):
        findings.append(Finding("canonical-identity", "base_url must use https://"))
    parsed_base = urlparse(base)
    if not parsed_base.netloc:
        findings.append(Finding("canonical-identity", "base_url must include a host"))
    docs = manifest.get("docs_url")
    if docs and not (docs.startswith("https://") or docs.startswith("/")):
        findings.append(Finding("docs-link", "docs_url must be HTTPS or a same-host path"))
    files = sorted(root.rglob("*.html"))
    if not files:
        findings.append(Finding("artifact", "no HTML artifacts found"))
    expected_canonical = base.rstrip("/") + "/"
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root)
        if SECRET.search(text):
            findings.append(Finding("secret-scan", str(rel)))
        for raw in ATTR_URL.findall(text):
            decoded = unquote(raw)
            if ".." in decoded.split("?")[0].split("#")[0].split("/"):
                findings.append(Finding("path-traversal", f"{rel}: {raw}"))
            if PRIVATE_ORIGIN.search(decoded):
                findings.append(Finding("private-origin", f"{rel}: {raw}"))
            if SENSITIVE_QUERY.search(urlparse(decoded).query):
                findings.append(Finding("query-leakage", f"{rel}: {raw}"))
        if rel == Path("index.html"):
            match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', text, re.I)
            if not match or _url(match.group(1), base) != expected_canonical:
                findings.append(Finding("canonical-identity", "index.html canonical link does not match base_url"))
    robots = root / "robots.txt"
    sitemap = root / "sitemap.xml"
    if robots.exists() != sitemap.exists():
        findings.append(Finding("robots-sitemap", "robots.txt and sitemap.xml must be present together"))
    if robots.exists():
        robot_text = robots.read_text(encoding="utf-8", errors="replace")
        if "Sitemap:" not in robot_text:
            findings.append(Finding("robots-sitemap", "robots.txt must declare Sitemap"))
        elif not sitemap.exists():
            findings.append(Finding("robots-sitemap", "declared sitemap is missing"))
    if sitemap.exists():
        for item in re.findall(r"<loc>([^<]+)</loc>", sitemap.read_text(encoding="utf-8", errors="replace"), re.I):
            if _url(item, base).split("#", 1)[0].rstrip("/") != expected_canonical.rstrip("/") and urlparse(item).netloc != parsed_base.netloc:
                findings.append(Finding("robots-sitemap", f"sitemap URL has unexpected host: {item}"))
    nav = manifest.get("required_navigation", [])
    index = (root / "index.html").read_text(encoding="utf-8", errors="replace") if (root / "index.html").exists() else ""
    for link in nav:
        if link not in index:
            findings.append(Finding("navigation", f"required navigation link absent: {link}"))
    analytics = manifest.get("analytics", {})
    if analytics.get("enabled") and analytics.get("payload_fields"):
        forbidden = {"prompt", "email", "repo", "repository", "tenant", "secret", "token", "authenticated_content"}
        leaked = forbidden.intersection(map(str.lower, analytics["payload_fields"]))
        if leaked:
            findings.append(Finding("privacy-analytics", "forbidden payload fields: " + ", ".join(sorted(leaked))))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("artifact_root", type=Path)
    args = parser.parse_args(argv)
    findings = validate(json.loads(args.manifest.read_text(encoding="utf-8")), args.artifact_root)
    for finding in findings:
        print(f"FAIL [{finding.rule}] {finding.detail}")
    if not findings:
        print("PASS public surface invariants")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
