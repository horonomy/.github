#!/usr/bin/env python3
"""Tests for check_positioning.py (HORO-1740).

Fixtures are the real text this session actually shipped: the pre-fix
regression (true positive), the merged post-fix text (true negative), and
Horologium/Octans as deliberate exceptions (identity_scope_terms /
boundary.intrinsic respectively). Run: python3 scripts/test_check_positioning.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_positioning import GateError, check_text, load_record, self_test  # noqa: E402

RECORDS_DIR = Path(__file__).resolve().parent.parent / "metadata" / "positioning"


def _record(product: str) -> dict:
    return load_record(RECORDS_DIR / f"{product}.yaml")


class SelfTestRunsClean(unittest.TestCase):
    def test_every_shipped_record_passes_its_own_self_test(self) -> None:
        for product in ("fornax", "circinus", "ophiuchus", "libra-governor", "horologium", "octans"):
            with self.subTest(product=product):
                self_test(_record(product), RECORDS_DIR / f"{product}.yaml")


class FornaxRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.record = _record("fornax")

    def test_true_positive_pre_fix_identity(self) -> None:
        # fornax-core README.md before 09e6c22 (HORO-1735).
        text = (
            "Evidence-first agent-integrity system for coding agents "
            "(Claude Code, Codex, opencode)."
        )
        findings = check_text(text, "identity", self.record, location="t")
        kinds = {f.kind for f in findings}
        self.assertIn("forbidden_shorthand", kinds)

    def test_true_positive_hero_h1_coding_agent(self) -> None:
        # fornax-website Home.tsx before 00ee204.
        text = "What should you believe about what your coding agent just told you?"
        findings = check_text(text, "identity", self.record, location="t")
        self.assertTrue(any(f.term.lower() == "coding agent" for f in findings))

    def test_true_negative_post_fix_identity(self) -> None:
        # fornax-core README.md as merged (b8317800).
        text = (
            "Evidence-first execution-truth infrastructure for AI agents: it "
            "checks what an agent claims it did against the observable "
            "evidence of what actually ran."
        )
        self.assertEqual(check_text(text, "identity", self.record, location="t"), [])

    def test_true_negative_availability_names_hosts_freely(self) -> None:
        text = "Currently supports coding agents (Claude Code, Codex, opencode)"
        self.assertEqual(check_text(text, "availability", self.record, location="t"), [])

    def test_wedge_example_is_not_an_identity_finding(self) -> None:
        text = "A coding agent narrates what it did"
        # Wedge surface never runs the host-vocabulary check.
        findings = check_text(text, "wedge", self.record, location="t")
        self.assertEqual([f for f in findings if f.kind == "host_in_identity"], [])


class CircinusRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.record = _record("circinus")

    def test_true_positive_pre_fix_hero(self) -> None:
        text = (
            "A local-first Agent Trust Runtime for Claude Code. Circinus "
            "tracks provenance and authority across agent transformations "
            "and enforces at the point of action."
        )
        findings = check_text(text, "identity", self.record, location="t")
        self.assertTrue(any(f.term.lower() == "claude code" for f in findings))

    def test_true_positive_who_its_for(self) -> None:
        text = "Developers running Claude Code who want visibility into what an agent session would do."
        findings = check_text(text, "identity", self.record, location="t")
        self.assertTrue(any(f.kind == "host_in_identity" for f in findings))

    def test_true_negative_post_fix_hero_with_availability_clause(self) -> None:
        text = (
            "A local-first Agent Trust Runtime for AI agents. Circinus "
            "tracks provenance and authority across agent transformations "
            "and enforces at the point of action. Today's MVP runs on "
            "Claude Code only."
        )
        self.assertEqual(check_text(text, "identity", self.record, location="t"), [])

    def test_true_negative_availability_heading(self) -> None:
        self.assertEqual(
            check_text("Currently supports Claude Code", "availability", self.record, location="t"), []
        )


class LibraRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.record = _record("libra-governor")

    def test_true_positive_shorthand(self) -> None:
        text = "Libra is a data-plane daemon that sits between an agentic coding tool and the LLM provider."
        findings = check_text(text, "identity", self.record, location="t")
        self.assertTrue(any(f.kind == "forbidden_shorthand" for f in findings))

    def test_true_negative_post_fix_identity_with_parenthetical(self) -> None:
        text = self.record["identity"]["text"]
        self.assertEqual(check_text(text, "identity", self.record, location="t"), [])


class OphiuchusRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.record = _record("ophiuchus")

    def test_true_positive_shorthand(self) -> None:
        text = (
            "Ophiuchus is a Context Fabric: it moves the useful part of an "
            "AI-assisted engineering session across machines and across "
            "coding-agent tools."
        )
        findings = check_text(text, "identity", self.record, location="t")
        self.assertTrue(any(f.kind == "forbidden_shorthand" for f in findings))

    def test_true_negative_post_fix_identity(self) -> None:
        self.assertEqual(
            check_text(self.record["identity"]["text"], "identity", self.record, location="t"), []
        )


class HorologiumException(unittest.TestCase):
    def test_canonical_identity_passes_with_scope_terms(self) -> None:
        record = _record("horologium")
        self.assertEqual(check_text(record["identity"]["text"], "identity", record, location="t"), [])

    def test_removing_scope_terms_makes_it_fail(self) -> None:
        record = _record("horologium")
        stripped = dict(record)
        stripped["identity_scope_terms"] = []
        findings = check_text(record["identity"]["text"], "identity", stripped, location="t")
        self.assertTrue(any(f.term.lower().startswith("coding") for f in findings))


class OctansException(unittest.TestCase):
    def test_zero_hosts_requires_intrinsic_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "octans.yaml"
            bad.write_text(
                'schema_version: 1\nproduct: "octans"\n'
                'identity:\n  text: "Octans."\n  source: {repo: "x", path: "y"}\n'
                'current_hosts:\n  text: "none"\n  hosts: []\n  source: {repo: "x", path: "y"}\n',
                encoding="utf-8",
            )
            with self.assertRaises(GateError):
                load_record(bad)

    def test_forbidden_shorthand_still_fires(self) -> None:
        record = _record("octans")
        findings = check_text("AI-powered change safety for agents", "identity", record, location="t")
        self.assertTrue(any(f.kind == "forbidden_shorthand" for f in findings))


class SelfTestCatchesABrokenRecord(unittest.TestCase):
    def test_a_record_whose_identity_fails_its_own_check_is_a_gate_error(self) -> None:
        import check_positioning as cp

        broken = {
            "product": "broken",
            "identity": {"text": "A tool for Claude Code.", "source": {"repo": "x", "path": "y"}},
            "current_hosts": {"hosts": ["Claude Code"], "text": "Claude Code today"},
        }
        with self.assertRaises(cp.GateError):
            cp.self_test(broken, Path("broken.yaml"))


class WordBoundarySanity(unittest.TestCase):
    def test_substring_does_not_false_positive(self) -> None:
        record = _record("fornax")
        text = "Evidence-first execution-truth infrastructure for AI agents, used while decoding agents' output."
        findings = check_text(text, "identity", record, location="t")
        self.assertEqual([f for f in findings if f.term.lower() == "coding agents"], [])

    def test_possessive_form_matches(self) -> None:
        record = _record("fornax")
        text = "A tool built for Codex's own workflow, as the sole identity."
        findings = check_text(text, "identity", record, location="t")
        self.assertTrue(any(f.term == "Codex" for f in findings))


class HtmlModeSelectors(unittest.TestCase):
    def _tree(self, html_text: str):
        import check_positioning as cp

        tree = cp._TreeBuilder()
        tree.feed(html_text)
        return tree.root

    def test_meta_description_content_attr(self) -> None:
        import check_positioning as cp

        root = self._tree(
            '<html><head><meta name="description" content="A tool for Claude Code."></head></html>'
        )
        nodes = cp.select(root, 'meta[name=description]')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(cp.node_content(nodes[0], 'meta[name=description]'), "A tool for Claude Code.")

    def test_descendant_selector(self) -> None:
        import check_positioning as cp

        root = self._tree("<header><p>Runtime for Claude Code.</p></header><p>Unrelated.</p>")
        nodes = cp.select(root, "header p")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(cp.node_content(nodes[0], "header p").strip(), "Runtime for Claude Code.")

    def test_end_to_end_html_finding(self) -> None:
        record = _record("circinus")
        root = self._tree(
            '<html><head><meta name="description" '
            'content="A local-first Agent Trust Runtime for Claude Code."></head></html>'
        )
        import check_positioning as cp

        node = cp.select(root, 'meta[name=description]')[0]
        content = cp.node_content(node, 'meta[name=description]')
        findings = check_text(content, "identity", record, location="page meta[name=description]")
        self.assertTrue(any(f.kind == "host_in_identity" for f in findings))


if __name__ == "__main__":
    unittest.main()
