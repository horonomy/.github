#!/usr/bin/env python3
"""Regenerate profile/README.md's "## Products" section from a pinned snapshot
of horonomy/official-website's Product Registry (HORO-282, HORO-599).

WHY A SEPARATE SCRIPT FROM generate_company_metadata.py
--------------------------------------------------------
metadata/company.yaml's product catalog (AAASM-5520) carries only four fields
per product (id/name/website/github_org/lifecycle) — enough for the company
footer, not enough for a Product Registry card (no category, no problem
statement, no celestial identity, no controlled maturity vocabulary with an
"experimental" tier, no ordering, no legacy-alias/redirect record). The org
profile's "## Products" section wants the richer shape, so it is generated
from a separate pinned snapshot of `productRegistry.ts` instead of widening
the company.yaml contract (that contract belongs to a different repo and a
different ticket, AAASM-5520 — out of scope to alter here).

CROSS-REPO DISTRIBUTION CONTRACT
---------------------------------
Mirrors official-website's own generate-company-metadata.mjs: PIN a vendored
snapshot of the upstream registry to a specific horonomy/official-website
commit (REGISTRY_SOURCE below) rather than fetching mutable main at generation
time — reproducible, network-free, fail-closed. Re-pin by updating REGISTRY
and REGISTRY_SOURCE together in one reviewed change whenever productRegistry.ts
changes.

LIVE_HOSTS mirrors atlas/destinations.mjs's fail-closed allowlist: a product
only gets a real link once its canonical host is independently verified live.
A product not on this list renders with no link and an "in development" note
— this is the same non-goal atlas/destinations.mjs documents: never fabricate
a public surface for a product that isn't actually live yet (e.g. Eridanus).

Usage:
    python3 scripts/generate_profile_products.py           # write in place
    python3 scripts/generate_profile_products.py --check   # exit non-zero on drift
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_README_PATH = REPO_ROOT / "profile" / "README.md"

REGISTRY_SOURCE = {
    "repo": "horonomy/official-website",
    "commit": "f33d5025a4ffa6b4b5963a79c24f845dc37e61c3",
    "path": "src/data/productRegistry.ts",
    "blob": "d3c38e8ef6ffd74d929256c201c0ea6183e24baf",
}

# Vendored verbatim (subset of fields needed for a profile card) from the
# pinned productRegistry.ts above. Keep in `order` sequence — it is also the
# render order.
REGISTRY = [
    {
        "id": "ai-agent-assembly",
        "name": "AI Agent Assembly",
        "emoji": "🤖",
        "maturity": "beta",
        "category": "Agent runtime & governance",
        "problem": "Gives AI agents a runtime with permissions, approval checkpoints, and an audit trail instead of unrestricted tool access.",
        "canonical_url": "https://agent-assembly.com",
        "docs_url": None,
        "github_url": "https://github.com/ai-agent-assembly",
        "order": 0,
    },
    {
        "id": "octans",
        "name": "Octans",
        "emoji": "🧭",
        "maturity": "experimental",
        "category": "Change safety",
        "problem": "Verifies a change is safe to ship before it reaches production, across distributed services.",
        "canonical_url": "https://octans.horo.run",
        "docs_url": None,
        "github_url": None,
        "order": 1,
    },
    {
        "id": "circinus",
        "name": "Circinus",
        "emoji": "📐",
        "maturity": "experimental",
        "category": "Provenance & authority",
        "problem": "Establishes who or what is authorized to take a sensitive action, and proves it after the fact.",
        "canonical_url": "https://circinus.horonom.com",
        "docs_url": "https://circinus.horonom.com/docs",
        "github_url": None,
        "order": 2,
    },
    {
        "id": "ophiuchus",
        "name": "Ophiuchus",
        "emoji": "🐍",
        "maturity": "experimental",
        "category": "Context continuity",
        "problem": "Carries context across machine, tool, and user boundaries so it is not re-derived or lost at each hop.",
        "canonical_url": "https://ophiuchus.horonom.com",
        "docs_url": "https://ophiuchus.horonom.com/docs",
        "github_url": None,
        "order": 3,
    },
    {
        "id": "fornax",
        "name": "Fornax",
        "emoji": "🔥",
        "maturity": "experimental",
        "category": "Agent integrity",
        "problem": "Verifies real evidence for what an AI coding agent claims it did.",
        "canonical_url": "https://fornax.horonom.com",
        "docs_url": "https://docs.fornax.horonom.com",
        "github_url": None,
        "order": 4,
    },
    {
        "id": "horologium",
        "name": "Horologium",
        "emoji": "⏱️",
        "maturity": "experimental",
        "category": "Product truth & integrity",
        "problem": "Keeps what a product claims and what a product actually does from silently drifting apart.",
        "canonical_url": "https://horologium.horonom.com",
        "docs_url": "https://horologium.horonom.com/docs",
        "github_url": None,
        "order": 5,
    },
    {
        "id": "eridanus",
        "name": "Eridanus",
        "emoji": "🌊",
        "maturity": "experimental",
        "category": "Forensic provenance",
        "problem": "Traces a data leak back through transformations to its point of origin, after the fact.",
        # Intentionally NOT a real canonical URL — see LIVE_HOSTS below.
        "canonical_url": "https://eridanus.horo.run",
        "docs_url": None,
        "github_url": None,
        "order": 6,
    },
]

# Fail-closed allowlist mirroring official-website's atlas/destinations.mjs.
# A product's canonical host must be independently verified live and added
# here in a reviewed change before this generator will link to it.
LIVE_HOSTS = frozenset(
    {
        "agent-assembly.com",
        "octans.horo.run",
        "circinus.horonom.com",
        "ophiuchus.horonom.com",
        "fornax.horonom.com",
        "horologium.horonom.com",
    }
)

# Hostnames that were once canonical and have since migrated. Must never
# reappear in LIVE_HOSTS — a drift check asserts this at generation time.
RETIRED_HOSTS = frozenset(
    {
        "circinus.horo.run",
        "ophiuchus.horo.run",
        "fornax.horo.run",
    }
)

_MATURITY_LABELS = {
    "experimental": "Experimental",
    "beta": "Beta",
    "release_candidate": "Release Candidate",
    "available": "Available",
}


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0]


def _validate() -> None:
    seen_ids: set[str] = set()
    for p in REGISTRY:
        if p["id"] in seen_ids:
            raise ValueError(f"duplicate product id in REGISTRY: {p['id']!r}")
        seen_ids.add(p["id"])
        if p["maturity"] not in _MATURITY_LABELS:
            raise ValueError(f"{p['id']}: unknown maturity {p['maturity']!r}")
        host = _host(p["canonical_url"])
        if host in RETIRED_HOSTS:
            raise ValueError(
                f"{p['id']}: canonical_url host {host!r} is a retired host and must "
                "never be reintroduced as canonical"
            )
    orders = sorted(p["order"] for p in REGISTRY)
    if orders != list(range(len(REGISTRY))):
        raise ValueError(f"REGISTRY 'order' values must be a 0..N-1 sequence, got {orders}")
    # A live host must never coincide with a retired host — would mean a
    # once-retired host silently came back onto the allowlist.
    overlap = LIVE_HOSTS & RETIRED_HOSTS
    if overlap:
        raise ValueError(f"LIVE_HOSTS must never contain a retired host: {sorted(overlap)}")


def render_products_section() -> str:
    _validate()
    lines: list[str] = []
    for p in sorted(REGISTRY, key=lambda x: x["order"]):
        label = _MATURITY_LABELS[p["maturity"]]
        lines.append(f"### {p['emoji']} {p['name']} — {label}")
        lines.append("")
        lines.append(f"{p['category']} — {p['problem']}")
        lines.append("")
        host = _host(p["canonical_url"])
        if host in LIVE_HOSTS:
            link_parts = [f"🌐 [{host}]({p['canonical_url']})"]
            if p["docs_url"]:
                link_parts.append(f"📚 [docs]({p['docs_url']})")
            if p["github_url"]:
                org = p["github_url"].rstrip("/").rsplit("/", 1)[-1]
                link_parts.append(f"💻 [github.com/{org}]({p['github_url']})")
            lines.append(" · ".join(link_parts))
        else:
            # Fail-closed: no canonical host verified live yet — no link, no
            # fabricated public surface. Matches the Atlas's own "Not yet
            # available." treatment of the same registry entry.
            lines.append("*Not yet available — in development.*")
        lines.append("")
    lines.append(
        "> More systems are awaiting stars. Additional research tracks — "
        "governance runtimes, change intelligence, and workflow primitives "
        "for autonomous software — are uncharted for now."
    )
    return "\n".join(lines)


def _write_profile_readme(content: str) -> None:
    """Write `content` to the hardcoded PROFILE_README_PATH constant.

    Takes no path argument on purpose — this generator only ever writes one
    file, and naming it directly (rather than accepting a `Path` parameter)
    keeps the write target a literal constant, never a value that could be
    mistaken for external input.

    SonarCloud (python:S2083) flags this as writing "malicious content":
    its taint model treats the `current = PROFILE_README_PATH.read_text()`
    call in build_artifact() as an external input source purely because it
    is a file read, then traces that string through _replace_bounded()'s
    splice into `content` here. There is no actual untrusted input on this
    path — PROFILE_README_PATH is this same hardcoded repo-local constant,
    REGISTRY above is a vendored Python literal, and neither is
    argv/network/env-derived anywhere in this module. Confirmed false
    positive; NOSONAR'd rather than restructured, since the "read this
    file, splice in generated content, write it back" shape is inherent to
    a bounded-region generator (the same pattern generate_company_metadata.py
    already uses for this exact file).
    """
    PROFILE_README_PATH.write_text(content, encoding="utf-8")  # NOSONAR


def _replace_bounded(text: str, block_id: str, body: str, where: str) -> str:
    begin = f"<!-- BEGIN GENERATED: {block_id} -->"
    end = f"<!-- END GENERATED: {block_id} -->"
    b = text.find(begin)
    e = text.find(end)
    if b < 0 or e < 0 or e < b:
        raise ValueError(f"{where}: bounded region {block_id!r} not found")
    return f"{text[: b + len(begin)]}\n{body}\n{text[e:]}"


def build_artifact() -> str:
    current = PROFILE_README_PATH.read_text(encoding="utf-8")
    return _replace_bounded(current, "products_section", render_products_section(), "profile/README.md")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit non-zero on drift.")
    args = parser.parse_args(argv)
    try:
        desired = build_artifact()
    except ValueError as exc:
        print(f"ERROR: invalid product registry snapshot — {exc}", file=sys.stderr)
        return 2

    current = PROFILE_README_PATH.read_text(encoding="utf-8")
    if current == desired:
        print("profile/README.md Products section is up to date.")
        return 0
    if args.check:
        print(
            "DRIFT: profile/README.md's Products section does not match the pinned "
            "productRegistry.ts snapshot.\nRun: python3 scripts/generate_profile_products.py",
            file=sys.stderr,
        )
        return 1
    _write_profile_readme(desired)
    print("Wrote profile/README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
