"""Tests for generate_profile_products.py (HORO-599).

Covers the drift-prevention invariants the ticket calls for: a missing
product, a disagreeing canonical URL, a retired hostname reappearing, and an
unpublished product gaining a fabricated public link.
"""

from __future__ import annotations

import copy
import unittest

import generate_profile_products as gpp


class RenderIsStableTest(unittest.TestCase):
    def test_render_is_idempotent(self) -> None:
        first_render = gpp.render_products_section()
        second_render = gpp.render_products_section()
        self.assertEqual(first_render, second_render)

    def test_check_mode_passes_against_committed_file(self) -> None:
        # The committed profile/README.md must already match the generator's
        # output — this is what CI's --check run enforces on every PR.
        self.assertEqual(gpp.main(["--check"]), 0)

    def test_every_registry_product_is_rendered(self) -> None:
        section = gpp.render_products_section()
        for product in gpp.REGISTRY:
            self.assertIn(product["name"], section, f"{product['id']} missing from rendered section")


class DriftPreventionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original = copy.deepcopy(gpp.REGISTRY)

    def tearDown(self) -> None:
        gpp.REGISTRY[:] = self._original

    def test_retired_host_never_in_live_hosts(self) -> None:
        self.assertTrue(gpp.LIVE_HOSTS.isdisjoint(gpp.RETIRED_HOSTS))

    def test_retired_host_reappearing_is_rejected(self) -> None:
        gpp.REGISTRY[2]["canonical_url"] = "https://circinus.horo.run"  # a retired host
        with self.assertRaisesRegex(ValueError, "retired host"):
            gpp.render_products_section()

    def test_unpublished_product_gets_no_link(self) -> None:
        section = gpp.render_products_section()
        eridanus_block = section.split("### 🌊 Eridanus")[1].split("### ")[0]
        self.assertIn("Not yet available", eridanus_block)
        self.assertNotIn("eridanus.horo.run", eridanus_block)
        self.assertNotIn("http", eridanus_block)

    def test_duplicate_product_id_is_rejected(self) -> None:
        gpp.REGISTRY.append(dict(gpp.REGISTRY[0]))
        with self.assertRaisesRegex(ValueError, "duplicate product id"):
            gpp.render_products_section()

    def test_unknown_maturity_is_rejected(self) -> None:
        gpp.REGISTRY[0]["maturity"] = "ga"  # not a controlled vocabulary value
        with self.assertRaisesRegex(ValueError, "unknown maturity"):
            gpp.render_products_section()

    def test_order_sequence_must_be_contiguous_from_zero(self) -> None:
        gpp.REGISTRY[0]["order"] = 99
        with self.assertRaisesRegex(ValueError, "order"):
            gpp.render_products_section()


if __name__ == "__main__":
    unittest.main()
