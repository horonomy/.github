"""Tests for generate_profile_products.py (HORO-599).

Covers the drift-prevention invariants the ticket calls for: a missing
product, a disagreeing canonical URL, a retired hostname reappearing, and an
unpublished product gaining a fabricated public link.
"""

from __future__ import annotations

import copy
import unittest
from unittest import mock

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

    def test_every_live_registry_product_is_rendered(self) -> None:
        section = gpp.render_products_section()
        for product in gpp.REGISTRY:
            host = gpp._host(product["canonical_url"])
            if host in gpp.LIVE_HOSTS:
                self.assertIn(product["name"], section, f"{product['id']} missing from rendered section")

    def test_write_mode_updates_the_file_when_drifted(self) -> None:
        # Exercise main()'s write branch (only --check is covered above) by
        # feeding it a deliberately stale "current" file content.
        with mock.patch.object(
            gpp.PROFILE_README_PATH.__class__,
            "read_text",
            return_value="<!-- BEGIN GENERATED: products_section -->\nstale\n<!-- END GENERATED: products_section -->\n",
        ), mock.patch.object(gpp, "_write_profile_readme") as write_mock:
            self.assertEqual(gpp.main([]), 0)
        write_mock.assert_called_once()
        (written_content,) = write_mock.call_args.args
        self.assertIn("AI Agent Assembly", written_content)
        self.assertNotIn("stale", written_content)


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

    def test_unpublished_product_is_omitted_entirely(self) -> None:
        # HORO-688: a not-yet-live product must not appear at all, not even
        # unlinked — public_release_reconcile.py's org_profile check treats
        # any bare mention of a not_yet_public product's name as premature
        # exposure, regardless of whether it carries a link.
        section = gpp.render_products_section()
        self.assertNotIn("Eridanus", section)
        self.assertNotIn("eridanus.horo.run", section)

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
