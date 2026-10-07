#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import first_party_converge as fc

ROOT = Path(__file__).resolve().parent.parent


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def __call__(self, argv, *, cwd=None, timeout=120, check=False, env=None):
        key = tuple(argv)
        self.calls.append((key, cwd))
        rc, stdout, stderr = self.responses.get(key, (0, "", ""))
        result = subprocess.CompletedProcess(list(argv), rc, stdout, stderr)
        if check and rc:
            raise fc.ConvergenceError(f"{argv[0]} failed")
        return result


def source(remote: str = "a" * 40, local: str | None = None) -> fc.SourceState:
    return fc.SourceState(
        checkout=Path("/safe/product"),
        remote="upstream",
        base_branch="main",
        remote_head=remote,
        local_head=local or remote,
        clean=True,
        main_worktree=True,
    )


class InventoryBoundaryTest(unittest.TestCase):
    def test_real_inventory_is_positive_and_third_party_rows_have_no_commands(self):
        inventory = fc.load_inventory()
        ids = {product.id for product in inventory.products}
        self.assertEqual(
            ids,
            {"libra", "circinus", "fornax", "ophiuchus", "horologium", "octans", "eridanus"},
        )
        exclusions = {item.id for item in inventory.exclusions}
        self.assertEqual(exclusions, {"archify", "visual-explainer"})
        raw = json.loads(fc.INVENTORY_PATH.read_text())
        for exclusion in raw["third_party_exclusions"]:
            self.assertEqual(set(exclusion), {"id", "owner", "repository", "policy_owner"})

    def test_unknown_and_third_party_ids_are_never_selected_for_action(self):
        inventory = fc.load_inventory()
        runner = FakeRunner()
        for item in ("archify", "visual-explainer", "random-plugin", "unknown"):
            with self.subTest(item=item), self.assertRaises(fc.ConvergenceError):
                fc.inspect_inventory(
                    inventory,
                    Path("/tmp/nowhere"),
                    runner,
                    product_ids={item},
                    validate=False,
                )
        self.assertEqual(runner.calls, [])

    def test_malformed_valid_json_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            fc, "receipt_root", return_value=Path(directory)
        ):
            (Path(directory) / "libra.json").write_text("[]")
            self.assertIsNone(fc._read_receipt("libra"))

    def test_inventory_rejects_checkout_escape(self):
        raw = json.loads(fc.INVENTORY_PATH.read_text())
        raw["products"][0]["checkout"] = "../../outside"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(fc.ConvergenceError, "unsafe checkout"):
                fc.load_inventory(path)

    def test_nonmanaged_product_cannot_smuggle_commands(self):
        raw = json.loads(fc.INVENTORY_PATH.read_text())
        raw["products"][2]["installer"] = ["evil"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(fc.ConvergenceError, "may not carry executable"):
                fc.load_inventory(path)


class RemoteResolutionTest(unittest.TestCase):
    def test_canonical_remote_need_not_be_origin_and_default_branch_comes_from_symref(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "libra-governor"
            (checkout / ".git").mkdir(parents=True)
            product = fc.load_inventory().products[0]
            sha = "b" * 40
            runner = FakeRunner(
                {
                    ("git", "status", "--porcelain", "--untracked-files=no"): (0, "", ""),
                    ("git", "remote"): (0, "origin\ncompany\n", ""),
                    ("git", "remote", "get-url", "origin"): (0, "https://github.com/person/fork.git\n", ""),
                    ("git", "remote", "get-url", "company"): (0, "https://github.com/horonomy/libra-governor.git\n", ""),
                    ("git", "ls-remote", "--symref", "company", "HEAD"): (
                        0,
                        f"ref: refs/heads/trunk\tHEAD\n{sha}\tHEAD\n",
                        "",
                    ),
                    ("git", "ls-remote", "company", "refs/heads/trunk"): (0, f"{sha}\trefs/heads/trunk\n", ""),
                    ("git", "rev-parse", "HEAD"): (0, sha + "\n", ""),
                    ("git", "branch", "--show-current"): (0, "trunk\n", ""),
                }
            )
            state = fc.inspect_source(product, Path(directory), runner)
            self.assertIsNone(state.error)
            self.assertEqual(state.remote, "company")
            self.assertEqual(state.base_branch, "trunk")
            self.assertEqual(state.remote_head, sha)

    def test_symlinked_git_metadata_is_not_a_managed_checkout(self):
        product = fc.load_inventory().products[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside-git"
            outside.mkdir()
            checkout = root / product.checkout
            checkout.mkdir()
            (checkout / ".git").symlink_to(outside, target_is_directory=True)
            self.assertIsNone(fc._checkout_path(root, product))

    def test_remote_failure_is_unverifiable_not_current(self):
        product = fc.load_inventory().products[0]
        state = fc.SourceState(error="remote_head_unresolved")
        self.assertEqual(fc.derive_status(product, state, {}, {"status": "not_run"}), "UNVERIFIABLE")


class StatusDerivationTest(unittest.TestCase):
    def setUp(self):
        self.product = fc.load_inventory().products[0]
        self.sha = "c" * 40

    def test_healthy_stale_runtime_is_drifted_even_with_binary_hooks_and_receipt(self):
        status = fc.derive_status(
            self.product,
            source(self.sha),
            {
                "installed_revision": "d" * 40,
                "running_revision": "d" * 40,
                "running_healthy": True,
                "binary_exists": True,
                "hooks_installed": True,
                "receipt_exists": True,
            },
            {"status": "passed"},
        )
        self.assertEqual(status, "DRIFTED")

    def test_unverified_installed_or_running_revision_is_not_current(self):
        for runtime in (
            {"installed_revision": None, "running_revision": self.sha, "running_healthy": True},
            {"installed_revision": self.sha, "running_revision": None, "running_healthy": True},
        ):
            with self.subTest(runtime=runtime):
                self.assertEqual(
                    fc.derive_status(self.product, source(self.sha), runtime, {"status": "passed"}),
                    "UNVERIFIABLE",
                )

    def test_only_exact_base_head_convergence_is_current(self):
        runtime = {
            "installed_revision": self.sha,
            "running_revision": self.sha,
            "running_healthy": True,
        }
        self.assertEqual(fc.derive_status(self.product, source(self.sha), runtime, {"status": "passed"}), "CURRENT")
        self.assertEqual(
            fc.derive_status(self.product, source(self.sha, "e" * 40), runtime, {"status": "not_run"}),
            "DRIFTED",
        )

    def test_unhealthy_matching_revisions_are_not_current(self):
        runtime = {
            "installed_revision": self.sha,
            "running_revision": self.sha,
            "running_healthy": False,
        }
        self.assertEqual(
            fc.derive_status(self.product, source(self.sha), runtime, {"status": "passed"}),
            "UNVERIFIABLE",
        )

    def test_red_base_head_is_explicitly_not_deployable(self):
        status = fc.derive_status(
            self.product,
            source(self.sha),
            {"installed_revision": "f" * 40, "running_revision": "f" * 40},
            {"status": "failed"},
        )
        self.assertEqual(status, "BASE_HEAD_NOT_DEPLOYABLE")


class ApplySafetyTest(unittest.TestCase):
    def test_failed_validation_never_invokes_installer_or_restart(self):
        product = fc.load_inventory().products[0]
        sha = "1" * 40
        runner = FakeRunner({product.validation[0]: (1, "", "fail")})
        with mock.patch.object(fc, "inspect_source", return_value=source(sha)), mock.patch.object(
            fc, "load_inventory", return_value=fc.load_inventory()
        ), mock.patch.object(fc, "inspect_runtime", return_value={}), mock.patch.object(
            fc, "inspect_product", return_value={"status": "BASE_HEAD_NOT_DEPLOYABLE"}
        ):
            result = fc.apply_product(product, Path("/tmp"), fc.load_inventory().digest, runner)
        self.assertEqual(result["status"], "BASE_HEAD_NOT_DEPLOYABLE")
        flat = [call for call, _cwd in runner.calls]
        for installer_command in product.installer or ():
            self.assertNotIn(installer_command, flat)
        self.assertFalse(any("stop" in call for call in flat))

    def test_remote_advance_after_validation_aborts_before_installer(self):
        product = fc.load_inventory().products[0]
        old = source("3" * 40)
        new = source("4" * 40)
        states = iter([old, old, new])
        runner = FakeRunner()
        with mock.patch.object(fc, "inspect_source", side_effect=lambda *_: next(states)), mock.patch.object(
            fc, "run_validation", return_value={"status": "passed", "commands": []}
        ), mock.patch.object(fc, "load_inventory", return_value=fc.load_inventory()):
            def apply():
                return fc.apply_product(product, Path("/tmp"), fc.load_inventory().digest, runner)

            with self.assertRaisesRegex(fc.ConvergenceError, "source changed after validation"):
                apply()
        flat = [call for call, _cwd in runner.calls]
        for command in product.installer or ():
            self.assertNotIn(command, flat)

    def test_unknown_prior_running_revision_never_stops_a_daemon(self):
        product = fc.load_inventory().products[0]
        sha = "5" * 40
        runner = FakeRunner()
        with mock.patch.object(fc, "inspect_source", return_value=source(sha)), mock.patch.object(
            fc, "run_validation", return_value={"status": "passed", "commands": []}
        ), mock.patch.object(fc, "load_inventory", return_value=fc.load_inventory()), mock.patch.object(
            fc, "inspect_runtime", return_value={"installed_revision": None, "running_revision": None, "running_healthy": True}
        ), mock.patch.object(fc, "_artifact_measurement", return_value="a" * 64), mock.patch.object(
            fc, "_write_receipt"
        ), mock.patch.object(fc, "inspect_product", return_value={"status": "UNVERIFIABLE"}):
            fc.apply_product(product, Path("/tmp"), fc.load_inventory().digest, runner)
        self.assertFalse(any("stop" in call for call, _cwd in runner.calls))

    def test_multistep_installer_runs_in_declared_order(self):
        product = fc.load_inventory().products[1]
        sha = "6" * 40
        runner = FakeRunner()
        runtime = {"installed_revision": sha, "running_revision": sha, "running_healthy": True}
        with mock.patch.object(fc, "inspect_source", return_value=source(sha)), mock.patch.object(
            fc, "run_validation", return_value={"status": "passed", "commands": []}
        ), mock.patch.object(fc, "load_inventory", return_value=fc.load_inventory()), mock.patch.object(
            fc, "inspect_runtime", return_value=runtime
        ), mock.patch.object(fc, "_artifact_measurement", return_value="b" * 64), mock.patch.object(
            fc, "_write_receipt"
        ), mock.patch.object(fc, "inspect_product", return_value={"status": "CURRENT"}):
            fc.apply_product(product, Path("/tmp"), fc.load_inventory().digest, runner)
        calls = [call for call, _cwd in runner.calls]
        indices = [calls.index(command) for command in product.installer or ()]
        self.assertEqual(indices, sorted(indices))

    def test_validation_timeout_is_unverifiable_not_a_traceback(self):
        product = fc.load_inventory().products[0]

        def timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("cargo", 600)

        result = fc.run_validation(product, source("7" * 40), timeout)
        self.assertEqual(result["status"], "unverifiable")

    def test_apply_never_accepts_a_feature_branch_as_target(self):
        product = fc.load_inventory().products[0]
        bad = source("2" * 40)
        bad.error = "not_on_configured_base_branch"
        def apply():
            return fc.apply_product(product, Path("/tmp"), fc.load_inventory().digest, FakeRunner())

        with mock.patch.object(fc, "inspect_source", return_value=bad):
            with self.assertRaisesRegex(fc.ConvergenceError, "not_on_configured_base_branch"):
                apply()

    def test_third_party_exclusions_are_never_apply_candidates(self):
        inventory = fc.load_inventory()
        self.assertTrue(all(product.id not in {x.id for x in inventory.exclusions} for product in inventory.products))
        source_text = Path(fc.__file__).read_text()
        self.assertNotIn("archify", source_text.lower())
        self.assertNotIn("visual-explainer", source_text.lower())
        self.assertNotIn("pkill", source_text)
        self.assertNotIn("cp ", source_text)
        self.assertNotIn("shell=True", source_text)


class RuntimeEvidenceTest(unittest.TestCase):
    def test_libra_doctor_requires_well_formed_all_non_error_findings(self):
        good = subprocess.CompletedProcess(
            [], 0, json.dumps({"findings": [{"severity": "ok", "message": "daemon 0.0.2 reachable"}]}), ""
        )
        mixed = subprocess.CompletedProcess(
            [],
            0,
            json.dumps({"findings": [
                {"severity": "ok", "message": "daemon 0.0.2 reachable"},
                {"severity": "error", "message": "stale runtime"},
            ]}),
            "",
        )
        malformed = subprocess.CompletedProcess([], 0, json.dumps({"findings": [None]}), "")
        self.assertTrue(fc._libra_doctor_healthy(good))
        self.assertFalse(fc._libra_doctor_healthy(mixed))
        self.assertFalse(fc._libra_doctor_healthy(malformed))
        self.assertFalse(fc._libra_doctor_healthy(subprocess.CompletedProcess([], 0, "[]", "")))

    def test_circinus_doctor_rejects_malformed_check_rows(self):
        runner = FakeRunner({
            ("circinus", "doctor", "--json"): (0, json.dumps({"checks": [None]}), "")
        })
        self.assertEqual(fc._circinus_doctor_state("circinus", runner), (False, False))


class CommandStatusTest(unittest.TestCase):
    def test_plan_fails_for_unverifiable_managed_product(self):
        report = {
            "first_party_products": [{"disposition": "managed", "status": "UNVERIFIABLE"}],
            "third_party_tools": [],
        }
        args = type("Args", (), {"root": "/tmp", "product": None, "json": True})()
        with mock.patch.object(fc, "load_inventory", return_value=fc.load_inventory()), mock.patch.object(
            fc, "inspect_inventory", return_value=report
        ), mock.patch("builtins.print"):
            self.assertEqual(fc._cmd_plan(args), 1)


if __name__ == "__main__":
    unittest.main()
