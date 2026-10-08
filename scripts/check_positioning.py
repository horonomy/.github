#!/usr/bin/env python3
"""Semantic copy-drift guard for product positioning (HORO-1740).

Catches a current host/adapter leaking into a product's canonical IDENTITY
(the Fornax "for coding agents" / Circinus "for Claude Code" regression),
without rejecting the same host name on an AVAILABILITY or WEDGE surface,
where naming it is correct and expected.

This is deliberately NOT a banned-word grep: whether a host name is a
defect depends on which canonical surface it appears on, and the
vocabulary of "current hosts" to flag is read per-product from
metadata/positioning/<product>.yaml (HORO-1733) — never a hardcoded global
list. A term a product has explicitly scoped as non-identity-bearing
(`identity_scope_terms`) or a product with no agent-host boundary at all
(`boundary.intrinsic`) is exempt by design, not by a human remembering to
add it to an exception list each time.

Usage
-----
Text mode (check one excerpt of a source file against one record):
    check_positioning.py text --product fornax --surface identity \\
        --record metadata/positioning/fornax.yaml --file README.md \\
        [--from '<literal>'] [--to '<literal>']

HTML mode (check a built static site against a surface map):
    check_positioning.py html --records-dir metadata/positioning \\
        --html-config surfaces.json --build-dir build

Source-verification mode (does the record still quote its T1 source?):
    check_positioning.py verify-source --record metadata/positioning/fornax.yaml \\
        --repo-root /path/to/checkouts

Exit codes
----------
0  clean — no findings.
1  one or more real findings (a genuine copy-drift candidate).
2  the gate itself is broken: bad CLI usage, a record that won't parse, an
   `identity_scope_terms`-less term still unmatched after self-test, a
   selector that matched zero nodes, or (most importantly) THIS SCRIPT'S
   OWN SELF-TEST failing — which must never be silently downgraded to a
   finding, because a broken gate that still exits 1 looks like "some
   copy needs fixing" instead of "do not trust this run at all".
"""

from __future__ import annotations

import argparse
import html.parser
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_company_metadata import YamlError, parse_yaml  # noqa: E402

AVAILABILITY_MARKERS = [
    "currently",
    "today's",
    "today",
    "available today",
    "in this mvp",
    "for now",
    "at present",
]


class GateError(Exception):
    """The gate itself is broken — exit 2, never downgraded to a finding."""


# ---------------------------------------------------------------------------
# Flow-value decoding
#
# parse_yaml() (generate_company_metadata.py) only understands block-style
# YAML. Every positioning record uses flow style for compact fields —
# `{repo: "...", path: "..."}` and `["Claude Code", "Codex"]` — which it
# returns as a single unparsed string. We decode exactly those two shapes
# ourselves rather than teaching the shared parser flow style, because the
# shared parser is depended on by company-metadata-drift and changing its
# behavior is out of this ticket's scope.
# ---------------------------------------------------------------------------
_FLOW_LIST_RE = re.compile(r"^\[(.*)\]$", re.DOTALL)
_FLOW_MAP_RE = re.compile(r"^\{(.*)\}$", re.DOTALL)
_FLOW_ITEM_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([^,]+)')
_FLOW_KV_RE = re.compile(r'(\w+)\s*:\s*("(?:[^"\\]|\\.)*"|[^,]+)')


def _decode_flow_scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('\\"', '"')
    return raw


def decode_flow(value: Any) -> Any:
    """Decode a `parse_yaml`-returned string if it looks like flow YAML."""
    if not isinstance(value, str):
        return value
    m = _FLOW_LIST_RE.match(value.strip())
    if m:
        items = []
        for mo in _FLOW_ITEM_RE.finditer(m.group(1)):
            piece = mo.group(1) if mo.group(1) is not None else mo.group(2)
            if piece is None:
                continue
            piece = piece.strip()
            if piece == "":
                continue
            items.append(_decode_flow_scalar(piece))
        return items
    m = _FLOW_MAP_RE.match(value.strip())
    if m:
        out: dict[str, str] = {}
        for mo in _FLOW_KV_RE.finditer(m.group(1)):
            out[mo.group(1)] = _decode_flow_scalar(mo.group(2))
        return out
    return value


def _decode_flow_tree(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _decode_flow_tree(decode_flow(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_decode_flow_tree(decode_flow(v)) for v in node]
    return decode_flow(node)


def load_record(path: Path) -> dict:
    try:
        raw = parse_yaml(path.read_text(encoding="utf-8"))
    except YamlError as exc:
        raise GateError(f"{path}: malformed positioning record: {exc}") from exc
    record = _decode_flow_tree(raw)
    if not isinstance(record, dict) or "product" not in record:
        raise GateError(f"{path}: not a valid positioning record (missing 'product')")

    hosts = record.get("current_hosts", {}).get("hosts", [])
    if not isinstance(hosts, list):
        raise GateError(f"{path}: current_hosts.hosts did not decode to a list")
    if hosts == [] and not record.get("boundary", {}).get("intrinsic"):
        raise GateError(
            f"{path}: current_hosts.hosts is empty but boundary.intrinsic is not "
            "set — an intentionally host-free product must say why, or this "
            "could just as easily be an unfinished record"
        )
    return record


# ---------------------------------------------------------------------------
# Text normalization + term matching
# ---------------------------------------------------------------------------
_ENTITY_MAP = {
    "&amp;": "&", "&quot;": '"', "&#39;": "'", "&apos;": "'",
    "’": "'", "‘": "'", "“": '"', "”": '"',
    " ": " ",
}
_TAG_RE = re.compile(r"<[^>]+>")
_JSX_EXPR_RE = re.compile(r"\{'\s*'\}")  # {' '} — a JSX literal space
_MD_EMPHASIS_RE = re.compile(r"[*`]+")  # **bold**, *italic*, `code` markers
_MD_BLOCKQUOTE_RE = re.compile(r"(?m)^>\s?")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    for src, dst in _ENTITY_MAP.items():
        text = text.replace(src, dst)
    text = _JSX_EXPR_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def term_pattern(term: str) -> re.Pattern[str]:
    """Case-insensitive, hyphen/space-flexible, optional plural/possessive."""
    escaped = re.escape(term.strip())
    flexible = escaped.replace(r"\ ", "[- ]").replace(r"\-", "[- ]")
    return re.compile(rf"\b{flexible}'?s?\b", re.IGNORECASE)


def find_terms(text: str, terms: list[str]) -> list[tuple[str, re.Match]]:
    hits = []
    for term in terms:
        if not term:
            continue
        for m in term_pattern(term).finditer(text):
            hits.append((term, m))
    return hits


_PAREN_RE = re.compile(r"\(([^()]*)\)")


def strip_availability_parens(text: str) -> str:
    """Drop any parenthetical that itself names an availability marker —
    `(Claude Code today)` — before sentence/clause analysis runs, so the
    host name inside it is never seen as an identity-surface claim."""
    def _maybe_drop(m: re.Match) -> str:
        inner = m.group(1).lower()
        if any(marker in inner for marker in AVAILABILITY_MARKERS):
            return " "
        return m.group(0)

    prev = text
    while True:
        nxt = _PAREN_RE.sub(_maybe_drop, prev)
        if nxt == prev:
            return nxt
        prev = nxt


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BOUNDARY_RE = re.compile(r"[,;:—-]\s*")


def split_sentences(text: str) -> list[str]:
    # A deliberately simple splitter — good enough for the short,
    # hand-written identity/availability sentences this guard reads, not a
    # general-purpose sentence boundary detector.
    return [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def truncate_at_marker_clause(sentence: str) -> str:
    """If an availability marker appears in `sentence`, return only the
    text up to the start of the clause that contains it, so a host name
    inside that trailing clause is never checked as identity."""
    lower = sentence.lower()
    earliest: int | None = None
    for marker in AVAILABILITY_MARKERS:
        idx = lower.find(marker)
        if idx != -1 and (earliest is None or idx < earliest):
            earliest = idx
    if earliest is None:
        return sentence
    # Walk back from the marker to the most recent clause boundary.
    clause_starts = [0] + [m.end() for m in _CLAUSE_BOUNDARY_RE.finditer(sentence[:earliest])]
    return sentence[: clause_starts[-1]]


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    product: str
    surface: str
    location: str
    kind: str  # "host_in_identity" | "forbidden_shorthand"
    term: str
    sentence: str
    identity_text: str
    identity_source: str
    reason: str = ""

    def render(self) -> str:
        lines = [f"FAIL {self.product} · surface={self.surface} · {self.location}"]
        if self.kind == "forbidden_shorthand":
            lines.append(f'  conflict: forbidden shorthand "{self.term}"')
            lines.append(f"  reason: {self.reason}")
        else:
            lines.append(
                f'  conflict: "{self.term}" (current_hosts) in an identity '
                "sentence with no availability qualifier"
            )
            lines.append(f'  sentence: "{self.sentence.strip()}"')
        lines.append(f"  canonical identity.text ({self.identity_source}): \"{self.identity_text}\"")
        lines.append(
            "  fix: keep identity host-free and move the host to an availability "
            'clause, e.g. "Currently supports <host>." If this term is intrinsic '
            "to the product's identity, add it to identity_scope_terms with a "
            "reason in this product's positioning record."
        )
        return "\n".join(lines)


def check_text(text: str, surface: str, record: dict, *, location: str) -> list[Finding]:
    if surface not in ("identity", "availability", "wedge"):
        raise GateError(f"unknown surface {surface!r} — expected identity/availability/wedge")

    product = record["product"]
    norm = normalize(text)
    findings: list[Finding] = []

    identity = record.get("identity", {})
    identity_text = identity.get("text", "")
    identity_source = ""
    src = identity.get("source")
    if isinstance(src, dict):
        identity_source = f"{src.get('repo', '?')} {src.get('path', '?')}"

    if surface in ("identity", "wedge"):
        for item in record.get("forbidden_shorthand", []) or []:
            phrase = item.get("phrase", "")
            if not phrase:
                continue
            if term_pattern(phrase).search(norm) or normalize(phrase) in norm:
                findings.append(
                    Finding(
                        product=product, surface=surface, location=location,
                        kind="forbidden_shorthand", term=phrase, sentence=norm,
                        identity_text=identity_text, identity_source=identity_source,
                        reason=item.get("reason", ""),
                    )
                )

    if surface != "identity":
        return findings

    current_hosts = record.get("current_hosts", {})
    hosts = current_hosts.get("hosts", []) or []
    host_class = current_hosts.get("host_class", []) or []
    scope_terms = {
        t.get("term", "").lower() for t in (record.get("identity_scope_terms") or [])
    }
    vocab = [h for h in [*hosts, *host_class] if h.lower() not in scope_terms]
    if not vocab:
        return findings

    stripped = strip_availability_parens(norm)
    for sentence in split_sentences(stripped):
        clause = truncate_at_marker_clause(sentence)
        for term, m in find_terms(clause, vocab):
            findings.append(
                Finding(
                    product=product, surface=surface, location=location,
                    kind="host_in_identity", term=term, sentence=sentence,
                    identity_text=identity_text, identity_source=identity_source,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Self-test — every invocation runs this against the record(s) it loaded,
# before doing anything else. A failure here means the gate cannot be
# trusted this run, which is exit 2, never exit 1.
# ---------------------------------------------------------------------------
def self_test(record: dict, record_path: Path) -> None:
    product = record["product"]

    identity_text = record.get("identity", {}).get("text", "")
    if check_text(identity_text, "identity", record, location=f"{record_path}#identity.text"):
        raise GateError(
            f"{product}: self-test failed — the record's own identity.text "
            "does not pass its own identity check"
        )

    hosts_text = record.get("current_hosts", {}).get("text", "")
    if hosts_text:
        findings = check_text(hosts_text, "availability", record, location=f"{record_path}#current_hosts.text")
        if findings:
            raise GateError(
                f"{product}: self-test failed — current_hosts.text produced a "
                f"finding on an availability surface: {findings[0].render()}"
            )

    wedge_text = record.get("wedge", {}).get("text", "")
    if wedge_text:
        check_text(wedge_text, "wedge", record, location=f"{record_path}#wedge.text")

    for item in record.get("forbidden_shorthand", []) or []:
        phrase = item.get("phrase", "")
        if not phrase:
            continue
        if not check_text(phrase, "identity", record, location=f"{record_path}#forbidden_shorthand"):
            raise GateError(
                f"{product}: self-test failed — forbidden_shorthand phrase "
                f"{phrase!r} does not fire on an identity surface"
            )

    # Word-boundary sanity: a substring of a vocabulary term must never
    # match ("decoding agents" must not trip "coding agents"), and a
    # possessive/plural form must ("Codex's" must trip "Codex").
    hosts = record.get("current_hosts", {}).get("hosts", []) or []
    host_class = record.get("current_hosts", {}).get("host_class", []) or []
    for term in [*hosts, *host_class]:
        pat = term_pattern(term)
        if pat.search(f"de{term.lower()}"):
            raise GateError(f"{product}: self-test failed — term {term!r} matched inside a longer word")
        if not pat.search(f"{term}'s own thing"):
            raise GateError(f"{product}: self-test failed — term {term!r} did not match its possessive form")


# ---------------------------------------------------------------------------
# HTML mode — a minimal selector subset: `tag`, `tag.class`, `#id`,
# `tag[attr=val]`, and one descendant level ("header p"). No external
# dependency; stdlib html.parser only.
# ---------------------------------------------------------------------------
@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    text_parts: list[str] = field(default_factory=list)
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)

    def text(self) -> str:
        return "".join(self.text_parts) + "".join(c.text() for c in self.children)


class _TreeBuilder(html.parser.HTMLParser):
    _VOID = {"br", "img", "meta", "link", "input", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node(tag="#root", attrs={})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag=tag, attrs={k: (v or "") for k, v in attrs}, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in self._VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        self._stack[-1].text_parts.append(data)


def _matches_simple(node: _Node, sel: str) -> bool:
    if sel.startswith("#"):
        return node.attrs.get("id") == sel[1:]
    tag, _, cls = sel.partition(".")
    attr_match = re.match(r"^([a-zA-Z0-9]+)\[([a-zA-Z-]+)=(.+)\]$", sel)
    if attr_match:
        tag, attr, val = attr_match.groups()
        val = val.strip('"\'')
        if tag and node.tag != tag:
            return False
        return node.attrs.get(attr) == val
    if tag and node.tag != tag:
        return False
    if cls and cls not in node.attrs.get("class", "").split():
        return False
    return True


def _walk(node: _Node) -> list[_Node]:
    out = [node]
    for c in node.children:
        out.extend(_walk(c))
    return out


def select(root: _Node, selector: str) -> list[_Node]:
    parts = selector.strip().split()
    all_nodes = _walk(root)
    if len(parts) == 1:
        return [n for n in all_nodes if _matches_simple(n, parts[0])]
    ancestors = [n for n in all_nodes if _matches_simple(n, parts[0])]
    out = []
    for n in all_nodes:
        if not _matches_simple(n, parts[-1]):
            continue
        p = n.parent
        found = False
        while p is not None:
            if any(p is a for a in ancestors):
                found = True
                break
            p = p.parent
        if found:
            out.append(n)
    return out


def node_content(node: _Node, selector: str) -> str:
    # A <meta> tag's text is always empty; the content it's selected for
    # lives in its `content` attribute regardless of which attribute the
    # selector matched on (e.g. `meta[name=description]`).
    if node.tag == "meta" and "content" in node.attrs:
        return node.attrs["content"]
    return node.text()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cmd_text(args: argparse.Namespace) -> int:
    record = load_record(Path(args.record))
    self_test(record, Path(args.record))

    source = Path(args.file).read_text(encoding="utf-8")
    excerpt = source
    if args.from_ is not None:
        idx = source.find(args.from_)
        if idx == -1:
            raise GateError(f"--from literal not found in {args.file}")
        excerpt = source[idx:]
    if args.to is not None:
        idx = excerpt.find(args.to, len(args.from_ or ""))
        if idx != -1:
            excerpt = excerpt[:idx]

    findings = check_text(excerpt, args.surface, record, location=f"{args.file}")
    for f in findings:
        print(f.render(), file=sys.stderr)
    return 1 if findings else 0


def _cmd_html(args: argparse.Namespace) -> int:
    records_dir = Path(args.records_dir)
    config = json.loads(Path(args.html_config).read_text(encoding="utf-8"))
    record_cache: dict[str, dict] = {}
    all_findings: list[Finding] = []

    for page, surfaces in config.items():
        page_path = Path(args.build_dir) / page
        if not page_path.exists():
            raise GateError(f"{page_path}: built page not found — run the site build first")
        html_text = page_path.read_text(encoding="utf-8")
        tree = _TreeBuilder()
        tree.feed(html_text)

        for entry in surfaces:
            product, surface, selector = entry["product"], entry["surface"], entry["selector"]
            if product not in record_cache:
                record_cache[product] = load_record(records_dir / f"{product}.yaml")
                self_test(record_cache[product], records_dir / f"{product}.yaml")
            nodes = select(tree.root, selector)
            if not nodes:
                raise GateError(f"{page}: selector {selector!r} matched zero nodes — the gate cannot see this surface")
            for node in nodes:
                content = node_content(node, selector)
                all_findings.extend(
                    check_text(content, surface, record_cache[product], location=f"{page} {selector}")
                )

    for f in all_findings:
        print(f.render(), file=sys.stderr)
    return 1 if all_findings else 0


def _read_git_blob(repo_dir: Path, ref: str, path: str) -> str | None:
    """Read `path` as of `ref` via `git show`, never the live working tree —
    these repos have other active sessions mutating their checkouts, so a
    plain file read would be reading whatever happened to be on disk at
    the moment this ran, not a reviewed, reproducible state."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "show", f"{ref}:{path}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _cmd_verify_source(args: argparse.Namespace) -> int:
    record_path = Path(args.record)
    record = load_record(record_path)
    self_test(record, record_path)
    repo_root = Path(args.repo_root)
    verified_against = record.get("verified_against") or {}
    pinned_sha = verified_against.get("sha")

    problems = []
    for field_name in ("identity", "current_hosts", "wedge"):
        entry = record.get(field_name)
        if not entry or "text" not in entry:
            continue
        src = entry.get("source")
        if not isinstance(src, dict) or "repo" not in src or "path" not in src:
            continue
        repo_dir = repo_root / src["repo"].split("/")[-1]
        if not repo_dir.exists():
            problems.append(f"{field_name}: no checkout found at {repo_dir}")
            continue
        # Pin to the sha this record was verified against when its own repo
        # matches the field's source repo; otherwise fall back to that
        # repo's own remote default branch tip (read-only, never checked out).
        ref = pinned_sha if verified_against.get("repo") == src["repo"] and pinned_sha else "origin/HEAD"
        source_text = _read_git_blob(repo_dir, ref, src["path"])
        if source_text is None:
            problems.append(f"{field_name}: could not read {src['repo']}:{src['path']} @ {ref} via git show")
            continue
        if normalize(entry["text"]) not in normalize(source_text):
            problems.append(
                f"{field_name}.text is not a verbatim substring of "
                f"{src['repo']}:{src['path']} @ {ref} — fix the record to "
                "match the source, never the reverse"
            )

    for p in problems:
        print(f"FAIL {record['product']} · verify-source · {p}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    p_text = sub.add_parser("text")
    p_text.add_argument("--product", required=True)
    p_text.add_argument("--surface", required=True, choices=["identity", "availability", "wedge"])
    p_text.add_argument("--record", required=True)
    p_text.add_argument("--file", required=True)
    p_text.add_argument("--from", dest="from_", default=None)
    p_text.add_argument("--to", default=None)
    p_text.set_defaults(func=_cmd_text)

    p_html = sub.add_parser("html")
    p_html.add_argument("--records-dir", required=True)
    p_html.add_argument("--html-config", required=True)
    p_html.add_argument("--build-dir", required=True)
    p_html.set_defaults(func=_cmd_html)

    p_vs = sub.add_parser("verify-source")
    p_vs.add_argument("--record", required=True)
    p_vs.add_argument("--repo-root", required=True)
    p_vs.set_defaults(func=_cmd_verify_source)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GateError as exc:
        print(f"GATE BROKEN: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"GATE BROKEN: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
