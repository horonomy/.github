#!/usr/bin/env python3
"""Tests for the Horonomy company-metadata generator (AAASM-5520).

Stdlib unittest only, matching the generator. Run with:
    python3 -m unittest discover -s scripts
"""

from __future__ import annotations

import copy
import json
import unittest

import generate_company_metadata as gen


def _valid() -> dict:
    return {
        "schema_version": "1",
        "company": {"name": "Horonomy", "website": "https://horonomy.dev"},
        "contacts": {"hello": "hello@horonomy.dev", "security": "security@horonomy.dev"},
        "security_policy": {
            "acknowledgement": {"value": "72", "unit": "hours"},
            "initial_assessment": {"value": "7", "unit": "calendar_days"},
        },
        "products": [
            {
                "id": "ai-agent-assembly",
                "name": "AI Agent Assembly",
                "website": "https://agent-assembly.com",
                "github_org": "https://github.com/ai-agent-assembly",
                "lifecycle": "beta",
            },
            {"id": "archeweave", "name": "ArcheWeave", "website": None, "github_org": None, "lifecycle": "coming_soon"},
        ],
    }


class ValidTest(unittest.TestCase):
    def test_valid_validates_and_projects(self) -> None:
        data = _valid()
        gen.validate(data)
        proj = json.loads(gen.render_company_json(data))
        self.assertEqual(proj["company"]["website"], "https://horonomy.dev")
        self.assertEqual(proj["contacts"]["security"], "security@horonomy.dev")
        self.assertEqual(proj["security_policy"]["acknowledgement"], {"value": 72, "unit": "hours"})
        self.assertEqual([p["id"] for p in proj["products"]], ["ai-agent-assembly", "archeweave"])

    def test_security_block_renders_shared_sla(self) -> None:
        block = gen.render_security_block(_valid())
        self.assertIn("security@horonomy.dev", block)
        self.assertIn("Within 72 hours", block)
        self.assertIn("Within 7 calendar days", block)


class InvalidTest(unittest.TestCase):
    def _reject(self, mutate) -> None:
        data = _valid()
        mutate(data)
        with self.assertRaises(gen.SchemaError):
            gen.validate(data)

    def test_bad_email(self) -> None:
        self._reject(lambda d: d["contacts"].__setitem__("security", "nope"))

    def test_bad_website(self) -> None:
        self._reject(lambda d: d["company"].__setitem__("website", "horonomy.dev"))

    def test_bad_lifecycle(self) -> None:
        self._reject(lambda d: d["products"][0].__setitem__("lifecycle", "shipping"))

    def test_bad_sla_unit(self) -> None:
        self._reject(lambda d: d["security_policy"]["acknowledgement"].__setitem__("unit", "fortnights"))

    def test_duplicate_contact(self) -> None:
        self._reject(lambda d: d["contacts"].__setitem__("hello", "security@horonomy.dev"))

    def test_planted_secret_rejected(self) -> None:
        data = _valid()
        data["contacts"]["smtp_token"] = "AKIA" + ("A1B2C3D4" * 6)
        with self.assertRaises(gen.SchemaError):
            gen.validate(data)

    def test_missing_schema_version(self) -> None:
        self._reject(lambda d: d.pop("schema_version"))


class DoesNotRedefineProductDetailTest(unittest.TestCase):
    def test_projection_has_no_product_contact_or_sdk_detail(self) -> None:
        # The catalog references products but must not carry AA product contact
        # addresses, mail domains, SDK urls, or package ids.
        proj = json.loads(gen.render_company_json(_valid()))
        blob = json.dumps(proj)
        for forbidden in ("agent-assembly.com/python", "@agent-assembly", "no-reply@", "pypi.org", "npmjs"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
