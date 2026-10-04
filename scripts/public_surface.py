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
PRIVATE_ORIGIN = re.compile(r"localhost|127\.0\.0\.1|0\.0\.0\.0|::1|0:0:0:0:0:0:0:1|\.internal(?:[:/]|$)|run\.app(?:[:/]|$)|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+", re.I)
SENSITIVE_QUERY = re.compile(r"(?:token|secret|password|api[_-]?key|access[_-]?token|email)=", re.I)
ATTR_URL = re.compile(r"(?:href|src)\s*=\s*[\"']([^\"']+)", re.I)
INDEX_HTML = Path("index.html")


@dataclass(frozen=True)
class Finding:
    rule: str
    detail: str


def _url(value: str, base: str) -> str:
    return urljoin(base.rstrip("/") + "/", value)


def _safe_url(value: str, base_host: str) -> tuple[bool, str]:
    decoded = unquote(unquote(value)).replace("&amp;", "&")
    parsed = urlparse(decoded)
    if parsed.username or parsed.password or parsed.scheme not in ("", "http", "https"):
        return False, "malformed or credential-bearing URL"
    if parsed.netloc and parsed.hostname != base_host:
        return False, "cross-host URL"
    if PRIVATE_ORIGIN.search(parsed.netloc or decoded):
        return False, "private or internal origin"
    if any(part == ".." for part in parsed.path.split("/")):
        return False, "path traversal"
    if SENSITIVE_QUERY.search(parsed.query):
        return False, "sensitive query parameter"
    return True, ""


def validate(manifest: dict, root: Path) -> list[Finding]:  # NOSONAR
    """Return deterministic findings; an empty list means the artifact passes."""
    findings: list[Finding] = []
    base = manifest.get("base_url", "")
    if not base.startswith("https://"):
        findings.append(Finding("canonical-identity", "base_url must use https://"))
    parsed_base = urlparse(base)
    if not parsed_base.netloc:
        findings.append(Finding("canonical-identity", "base_url must include a host"))
    docs = manifest.get("docs_url")
    if docs:
        ok, reason = _safe_url(str(docs), parsed_base.hostname or "")
        if not ok or (str(docs).startswith("//")):
            findings.append(Finding("docs-link", f"docs_url rejected: {reason or 'invalid scheme'}"))
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".html", ".js", ".css", ".json", ".xml", ".map", ".txt"})
    if not files:
        findings.append(Finding("artifact", "no HTML artifacts found"))
    expected_canonical = base.rstrip("/") + "/"
    for path in files:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            findings.append(Finding("artifact-boundary", "artifact contains a symlink outside the output root"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root)
        if SECRET.search(text):
            findings.append(Finding("secret-scan", str(rel)))
        if SECRET.search(text): findings.append(Finding("secret-scan", str(rel)))
        if PRIVATE_ORIGIN.search(text): findings.append(Finding("private-origin", str(rel)))
        for raw in ATTR_URL.findall(text):
            ok, reason = _safe_url(raw, parsed_base.hostname or "")
            if not ok: findings.append(Finding("url-boundary", f"{rel}: {reason}"))
        if path.suffix.lower() == ".html":
            match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', text, re.I)
            if not match or urlparse(_url(match.group(1), base)).hostname != parsed_base.hostname:
                findings.append(Finding("canonical-identity", f"missing or foreign canonical in {rel}"))
            elif rel == INDEX_HTML and _url(match.group(1), base) != expected_canonical:
                findings.append(Finding("canonical-identity", "index.html canonical link does not match base_url"))
    robots = root / "robots.txt"
    sitemap = root / "sitemap.xml"
    if not robots.exists() or not sitemap.exists():
        findings.append(Finding("robots-sitemap", "robots.txt and sitemap.xml are mandatory"))
    if robots.exists():
        robot_text = robots.read_text(encoding="utf-8", errors="replace")
        if "Sitemap:" not in robot_text:
            findings.append(Finding("robots-sitemap", "robots.txt must declare Sitemap"))
        elif not sitemap.exists(): findings.append(Finding("robots-sitemap", "declared sitemap is missing"))
    if sitemap.exists():
        sitemap_text = sitemap.read_text(encoding="utf-8", errors="replace")
        items = re.findall(r"<loc>([^<]+)</loc>", sitemap_text, re.I)
        if not items: findings.append(Finding("robots-sitemap", "sitemap has no loc entries"))
        for item in items:
            ok, reason = _safe_url(item, parsed_base.hostname or "")
            if not ok: findings.append(Finding("robots-sitemap", f"sitemap URL rejected: {reason}"))
    nav = manifest.get("required_navigation", [])
    index = (root / INDEX_HTML).read_text(encoding="utf-8", errors="replace") if (root / INDEX_HTML).exists() else ""
    for link in nav:
        if not re.search(rf'<a\b[^>]+href=["\']{re.escape(link)}(?:[#"\'])', index, re.I):
            findings.append(Finding("navigation", "required navigation target absent or not an anchor"))
    analytics = manifest.get("analytics", {})
    if analytics.get("enabled") and not analytics.get("payload_fields"):
        findings.append(Finding("privacy-analytics", "enabled analytics must declare payload_fields"))
    if analytics.get("enabled") and analytics.get("payload_fields"):
        forbidden = {"prompt", "email", "repo", "repository", "tenant", "secret", "token", "authenticated_content"}
        leaked = forbidden.intersection(map(str.lower, analytics["payload_fields"]))
        if leaked:
            findings.append(Finding("privacy-analytics", "forbidden payload fields: " + ", ".join(sorted(leaked))))
        if re.search(r"(?:prompt|email|tenant|authenticated_content|api[_-]?key|access[_-]?token)\s*[:=]", " ".join(p.read_text(encoding="utf-8", errors="replace") for p in files), re.I):
            findings.append(Finding("privacy-analytics", "artifact contains a sensitive analytics field"))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("artifact_root", type=Path)
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve(strict=True)
    artifact_root = args.artifact_root.resolve(strict=True)
    if not manifest_path.is_file() or not artifact_root.is_dir():
        parser.error("manifest must be a file and artifact_root must be a directory")
    findings = validate(json.loads(manifest_path.read_text(encoding="utf-8")), artifact_root)  # NOSONAR
    for finding in findings:
        print(f"FAIL [{finding.rule}] {finding.detail}")
    if not findings:
        print("PASS public surface invariants")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
