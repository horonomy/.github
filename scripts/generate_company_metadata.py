#!/usr/bin/env python3
"""Regenerate Horonom company-metadata consumers from metadata/company.yaml.

metadata/company.yaml is the single public source of truth for MOTHER-COMPANY
facts (AAASM-5520): company name + website, the publicly-published company
contact addresses, the company's own structured security-response targets, and
a product CATALOG. This script validates it and derives:

  * metadata/generated/company.json  — a machine-readable projection that
    cross-repo consumers (e.g. horonomy/official-website) read instead of
    hand-copying a value. It is the pinned distribution artifact.
  * SECURITY.md  — the bounded `<!-- BEGIN GENERATED: company_contact -->` region
    (reporting address + structured SLAs).
  * profile/README.md — the bounded `<!-- BEGIN GENERATED: company_footer -->`
    region (company site + security-policy contact line).

Mirrors the ai-agent-assembly/.github generator's contract: stdlib only,
deterministic, idempotent (`--check` exits non-zero on drift), and fail-closed —
malformed or leakage-prone input aborts before any artifact is produced. It owns
ONLY company facts; it never redefines AI Agent Assembly product detail.

Usage:
    python3 scripts/generate_company_metadata.py
    python3 scripts/generate_company_metadata.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SOT_PATH = REPO_ROOT / "metadata" / "company.yaml"
GENERATED_DIR = REPO_ROOT / "metadata" / "generated"
COMPANY_JSON_PATH = GENERATED_DIR / "company.json"
SECURITY_PATH = REPO_ROOT / "SECURITY.md"
PROFILE_README_PATH = REPO_ROOT / "profile" / "README.md"

_EMAIL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._%+-]*@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^https://([A-Za-z0-9-]+\.)+[A-Za-z]{2,}(/\S*)?$")
_SLA_UNITS = frozenset({"business_days", "calendar_days", "hours"})
_LIFECYCLES = frozenset({"available", "beta", "release_candidate", "coming_soon"})

# Forbidden private/secret patterns — the public company registry must never
# carry operational or secret data. Mirrors the AA registry's guard.
_FORBIDDEN_KEY_SUBSTRINGS = (
    "recovery", "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "private_key", "privatekey", "dkim_private", "account_id",
    "phone", "pager", "oncall", "on_call",
)
_FORBIDDEN_LOCALPARTS = ("recovery", "admin", "administrator", "superadmin", "root", "oncall")
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]?){7,}")
_SECRET_VALUE_RES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    # Common named-prefix credential formats — the two generic patterns
    # above (long base64/hex runs) miss these because a `_` separator or
    # mixed-case non-hex letters break their charsets. Found live (HORO-533
    # security review): a planted GitHub PAT-shaped string
    # (`ghp_<36 chars>`) sailed straight through both generic patterns and
    # was reported PASS by doctor's no_secrets_in_generated_files check.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub PAT/OAuth/App/refresh tokens (classic)
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub fine-grained PAT
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS long-term (AKIA) / temporary STS (ASIA) access key ID
    re.compile(r"\bxox(?:[baprs]|e)-[A-Za-z0-9-]{10,}\b"),  # Slack tokens, incl. xoxe- refresh/exchange
    # OpenAI-style keys: the `-proj-`/`-org-`/etc. variants insert a hyphen
    # a few chars in, which broke a plain `sk-[A-Za-z0-9]{20,}` run — an
    # independent review (HORO-533) constructed an `sk-proj-...` value that
    # slipped past both that pattern and the generic base64/hex fallbacks.
    # Match the literal prefix, then allow embedded hyphens in the body.
    re.compile(r"\bsk-(?:proj-|org-|ant-)?[A-Za-z0-9-]{20,}\b"),
)

_SLA_UNIT_LABELS = {
    "business_days": ("business day", "business days"),
    "calendar_days": ("calendar day", "calendar days"),
    "hours": ("hour", "hours"),
}


class YamlError(RuntimeError):
    pass


class SchemaError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Minimal YAML subset parser (stdlib only). Supports: top-level scalars, nested
# maps, list-of-maps, quoted scalars, null, and inline comments outside quotes.
# ---------------------------------------------------------------------------
def _strip_comment(s: str) -> str:
    in_quote = False
    for i, c in enumerate(s):
        if c == '"' and (i == 0 or s[i - 1] != "\\"):
            in_quote = not in_quote
        elif c == "#" and not in_quote:
            return s[:i].rstrip()
    return s.rstrip()


def _unquote(v: str) -> Any:
    v = v.strip()
    if not v:
        return ""
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        return v[1:-1]
    if v == "null":
        return None
    if v == "true":
        return True
    if v == "false":
        return False
    return v


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_yaml(text: str) -> dict:
    raw_lines = text.splitlines()
    root: dict = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        line = _strip_comment(raw)
        if not line.strip():
            i += 1
            continue
        indent = _indent_of(line)
        content = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            raise YamlError(f"line {i+1}: no parent container")
        parent = stack[-1][1]

        if content.startswith("- "):
            if not isinstance(parent, list):
                raise YamlError(f"line {i+1}: list item under non-list")
            body = content[2:].strip()
            if ":" in body and not body.startswith('"'):
                new_map: dict = {}
                parent.append(new_map)
                stack.append((indent, new_map))
                raw_lines[i] = " " * (indent + 2) + body
                continue
            parent.append(_unquote(body))
            i += 1
            continue

        if ":" not in content:
            raise YamlError(f"line {i+1}: unrecognized syntax: {content!r}")
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        if isinstance(parent, list):
            raise YamlError(f"line {i+1}: mapping key inside a list without '-'")
        if rest == "":
            # Peek to decide list vs map.
            j = i + 1
            container: Any = {}
            while j < len(raw_lines):
                nxt = raw_lines[j]
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    j += 1
                    continue
                if _indent_of(nxt) <= indent:
                    break
                container = [] if nxt.lstrip().startswith("- ") else {}
                break
            parent[key] = container
            stack.append((indent, container))
            i += 1
            continue
        parent[key] = _unquote(rest)
        i += 1
    return root


# ---------------------------------------------------------------------------
# Validation (fail closed)
# ---------------------------------------------------------------------------
def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool):
        raise SchemaError(f"{where}: expected an integer, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise SchemaError(f"{where}: expected an integer, got {value!r}")


def _walk_forbidden(node: Any, path: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            low = str(k).lower()
            for sub in _FORBIDDEN_KEY_SUBSTRINGS:
                if sub in low:
                    raise SchemaError(f"{path}.{k}: forbidden key (matched {sub!r})")
            _walk_forbidden(v, f"{path}.{k}")
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            _walk_forbidden(item, f"{path}[{idx}]")
    elif isinstance(node, str):
        low = node.lower()
        for lp in _FORBIDDEN_LOCALPARTS:
            if low.startswith(lp + "@"):
                raise SchemaError(f"{path}: forbidden identity {node!r}")
        if _PHONE_RE.search(node):
            raise SchemaError(f"{path}: looks like a phone number")
        for rx in _SECRET_VALUE_RES:
            if rx.search(node):
                raise SchemaError(f"{path}: looks like a secret/token blob")


def validate(data: dict) -> None:
    if "schema_version" not in data:
        raise SchemaError("schema_version: required")
    if _as_int(data["schema_version"], "schema_version") < 1:
        raise SchemaError("schema_version: must be >= 1")

    _walk_forbidden(data.get("contacts", {}), "contacts")
    _walk_forbidden(data.get("company", {}), "company")

    company = data.get("company")
    if not isinstance(company, dict) or not company.get("name"):
        raise SchemaError("company.name: required")
    if not _URL_RE.match(str(company.get("website", ""))):
        raise SchemaError("company.website: must be an https:// URL")

    contacts = data.get("contacts")
    if not isinstance(contacts, dict) or not contacts:
        raise SchemaError("contacts: must be a non-empty mapping")
    seen: set[str] = set()
    for aud, addr in contacts.items():
        if not _EMAIL_RE.match(str(addr)):
            raise SchemaError(f"contacts.{aud}: {addr!r} is not a valid email")
        if str(addr).lower() in seen:
            raise SchemaError(f"contacts.{aud}: {addr!r} is not unique")
        seen.add(str(addr).lower())

    sp = data.get("security_policy")
    if not isinstance(sp, dict) or not sp:
        raise SchemaError("security_policy: must be a non-empty mapping")
    for target in ("acknowledgement", "initial_assessment"):
        blk = sp.get(target)
        if not isinstance(blk, dict) or "value" not in blk or "unit" not in blk:
            raise SchemaError(f"security_policy.{target}: needs value + unit")
        if _as_int(blk["value"], f"security_policy.{target}.value") <= 0:
            raise SchemaError(f"security_policy.{target}.value: must be positive")
        if blk["unit"] not in _SLA_UNITS:
            raise SchemaError(f"security_policy.{target}.unit: {blk['unit']!r} invalid")

    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise SchemaError("products: must be a non-empty list")
    seen_ids: set[str] = set()
    for idx, p in enumerate(products):
        base = f"products[{idx}]"
        if not isinstance(p, dict) or not p.get("id") or not p.get("name"):
            raise SchemaError(f"{base}: needs id + name")
        if p["id"] in seen_ids:
            raise SchemaError(f"{base}.id: {p['id']!r} is not unique")
        seen_ids.add(p["id"])
        if p.get("lifecycle") not in _LIFECYCLES:
            raise SchemaError(f"{base}.lifecycle: {p.get('lifecycle')!r} invalid")
        for url_key in ("website", "github_org"):
            val = p.get(url_key)
            if val is not None and not _URL_RE.match(str(val)):
                raise SchemaError(f"{base}.{url_key}: {val!r} must be an https:// URL or null")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _format_sla(block: dict, where: str) -> str:
    value = _as_int(block["value"], f"{where}.value")
    singular, plural = _SLA_UNIT_LABELS[block["unit"]]
    return f"{value} {singular if value == 1 else plural}"


def render_company_json(data: dict) -> str:
    products = [
        {
            "id": p["id"],
            "name": p["name"],
            "website": p.get("website"),
            "github_org": p.get("github_org"),
            "lifecycle": p.get("lifecycle"),
        }
        for p in data.get("products", [])
    ]
    sp = data.get("security_policy", {})
    projection = {
        "_readme": (
            "DO NOT EDIT. Generated by scripts/generate_company_metadata.py from "
            "metadata/company.yaml (Horonom company metadata registry, AAASM-5520)."
        ),
        "schema_version": _as_int(data["schema_version"], "schema_version"),
        "company": {
            "name": data["company"]["name"],
            "website": data["company"]["website"],
        },
        "contacts": dict(data.get("contacts", {})),
        "security_policy": {
            t: {"value": _as_int(sp[t]["value"], f"security_policy.{t}.value"), "unit": sp[t]["unit"]}
            for t in ("acknowledgement", "initial_assessment")
        },
        "products": products,
    }
    return json.dumps(projection, indent=2, ensure_ascii=False) + "\n"


def _replace_bounded(text: str, block_id: str, body: str, where: str) -> str:
    begin = f"<!-- BEGIN GENERATED: {block_id} -->"
    end = f"<!-- END GENERATED: {block_id} -->"
    b = text.find(begin)
    e = text.find(end)
    if b < 0 or e < 0 or e < b:
        raise SchemaError(f"{where}: bounded region {block_id!r} not found")
    return f"{text[: b + len(begin)]}\n{body}\n{text[e:]}"


def render_security_block(data: dict) -> str:
    name = data["company"]["name"]
    sec = data["contacts"]["security"]
    sp = data["security_policy"]
    ack = _format_sla(sp["acknowledgement"], "security_policy.acknowledgement")
    assess = _format_sla(sp["initial_assessment"], "security_policy.initial_assessment")
    return "\n".join(
        [
            f"If you discover a security vulnerability in any {name} repository, "
            f"please report it **privately** by emailing **{sec}**. Do not open a "
            "public GitHub issue or discussion for security issues.",
            "",
            "| Response stage | Target |",
            "| --- | --- |",
            f"| Acknowledgement | Within {ack} |",
            f"| Initial assessment | Within {assess} |",
        ]
    )


def render_profile_footer_block(data: dict) -> str:
    site = data["company"]["website"]
    sec = data["contacts"]["security"]
    return "\n".join(
        [
            f"- 🌐 Company site — <{site}>",
            "- 🤖 AI Agent Assembly org — <https://github.com/ai-agent-assembly>",
            "- 🤝 [Contributing](https://github.com/horonomy/.github/blob/main/CONTRIBUTING.md)",
            f"- 🔒 [Security policy](https://github.com/horonomy/.github/blob/main/SECURITY.md) — report privately to `{sec}`",
        ]
    )


def build_artifacts() -> dict[Path, str]:
    data = parse_yaml(SOT_PATH.read_text(encoding="utf-8"))
    validate(data)
    security = _replace_bounded(
        SECURITY_PATH.read_text(encoding="utf-8"),
        "company_contact",
        render_security_block(data),
        "SECURITY.md",
    )
    profile = _replace_bounded(
        PROFILE_README_PATH.read_text(encoding="utf-8"),
        "company_footer",
        render_profile_footer_block(data),
        "profile/README.md",
    )
    return {
        COMPANY_JSON_PATH: render_company_json(data),
        SECURITY_PATH: security,
        PROFILE_README_PATH: profile,
    }


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit non-zero on drift.")
    args = parser.parse_args(argv)
    try:
        artifacts = build_artifacts()
    except (YamlError, SchemaError) as exc:
        print(f"ERROR: invalid metadata/company.yaml — {exc}", file=sys.stderr)
        return 2
    drifted = [p for p, content in artifacts.items() if _read_or_empty(p) != content]
    if not drifted:
        print("Generated company-metadata artifacts are up to date.")
        return 0
    if args.check:
        for p in drifted:
            print(f"DRIFT: {p.relative_to(REPO_ROOT)} does not match metadata/company.yaml.", file=sys.stderr)
        print("Run: python3 scripts/generate_company_metadata.py", file=sys.stderr)
        return 1
    if COMPANY_JSON_PATH in drifted:
        COMPANY_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        COMPANY_JSON_PATH.write_text(artifacts[COMPANY_JSON_PATH], encoding="utf-8")
        print(f"Wrote {COMPANY_JSON_PATH.relative_to(REPO_ROOT)}.")
    if SECURITY_PATH in drifted:
        SECURITY_PATH.write_text(artifacts[SECURITY_PATH], encoding="utf-8")
        print(f"Wrote {SECURITY_PATH.relative_to(REPO_ROOT)}.")
    if PROFILE_README_PATH in drifted:
        PROFILE_README_PATH.write_text(artifacts[PROFILE_README_PATH], encoding="utf-8")
        print(f"Wrote {PROFILE_README_PATH.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
