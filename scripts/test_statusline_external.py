"""Tests for coexistence with an external owner of the host settings file.

The fixture store here is shaped like the real one (HORO-1660): two tables
holding whole JSON documents, rows for more than one host application, and
documents carrying keys this code has never heard of. A fixture with one table
and one row would make every assertion below indistinguishable from a no-op.

`replay_over_live` and `switch_over_live` are faithful ports of the two
destructive write paths that caused the defect. They are what make the central
claim -- "after seeding, the manager's own write carries the statusline" --
a demonstration rather than an assertion about intent.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import tempfile
import unittest

import statusline_external as external

COMPOSITOR = "/usr/bin/python3 /home/founder/.horonom/statusline-runtime/statusline_compositor.py"

# What the lifecycle computes and hands to this module: the one source of truth
# every seeded copy is compared against.
HORONOM_STATUS_LINE = {
    "type": "command",
    "command": COMPOSITOR,
    "padding": 0,
    external.MARKER_KEY: {"owner": "horonom-statusline", "version": 1},
}

USER_STATUS_LINE = {
    "type": "command",
    "command": "/home/founder/.claude/statusline.sh",
    "padding": 0,
    "futureUnknownKey": "keep-me",
}


def rich_document(status_line: dict | None) -> dict:
    """A stored document with something of every shape a careless write would lose."""
    data = {
        "model": "claude-opus-4",
        "theme": "dark-daltonized",
        "env": {
            "ANTHROPIC_BASE_URL": "https://gateway.example.invalid/v1?trailing=true",
            "API_TIMEOUT_MS": "600000",
        },
        "permissions": {"allow": ["Bash(git status)"], "deny": []},
        "enabledPlugins": {"rust-analyzer-lsp@claude-plugins-official": True},
        "futureUnknownKey": {"introducedIn": "a release this code predates", "keep": True},
    }
    if status_line is not None:
        data["statusLine"] = status_line
    return data


def sanitize_for_live(settings: dict) -> dict:
    """The manager's entire live-write transformation: four internal keys removed."""
    out = dict(settings)
    for key in ("api_format", "apiFormat", "openrouter_compat_mode", "openrouterCompatMode"):
        out.pop(key, None)
    return out


def replay_over_live(path: pathlib.Path, snapshot: dict) -> None:
    """The crash-recovery path: the stored snapshot written over live, unread."""
    path.write_text(json.dumps(sanitize_for_live(snapshot), indent=2) + "\n")


switch_over_live = replay_over_live  # The provider-switch path is the same write.

PROFILE_COLUMNS = "id TEXT, app_type TEXT, settings_config TEXT"


def build_store(
    store: pathlib.Path, *, profile_columns: str = PROFILE_COLUMNS, user_status_line: dict | None = None
) -> None:
    """A store shaped like the real one, at `store`.

    Module-level rather than a method so the lifecycle suite can build the same
    fixture: two suites asserting against two differently-shaped stores would make
    their results incomparable, which is the thing to avoid when one suite tests
    the adapter and the other tests the caller that drives it.
    """
    user = USER_STATUS_LINE if user_status_line is None else user_status_line
    store.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(store)
    with connection:
        connection.execute(f"CREATE TABLE providers ({profile_columns})")
        connection.execute("CREATE TABLE proxy_live_backup (app_type TEXT, original_config TEXT)")
        if "settings_config" in profile_columns:
            connection.executemany(
                "INSERT INTO providers VALUES (?, ?, ?)",
                [
                    ("p1", "claude", json.dumps(rich_document(user))),
                    ("p2", "claude", json.dumps(rich_document(None))),
                    # A row for a different host application: never ours.
                    ("p3", "codex", json.dumps(rich_document(user))),
                ],
            )
        connection.execute(
            "INSERT INTO proxy_live_backup VALUES (?, ?)",
            ("claude", json.dumps(rich_document(user))),
        )
    connection.close()


def stored_document(store: pathlib.Path, table: str, column: str, key_column: str, key: str) -> dict:
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        row = connection.execute(
            f"SELECT {column} FROM {table} WHERE {key_column} = ?", (key,)
        ).fetchone()
    finally:
        connection.close()
    return json.loads(row[0])


class ExternalCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = self.root / ".cc-switch" / "cc-switch.db"
        self.build_store()

    def build_store(self, *, profile_columns: str = PROFILE_COLUMNS) -> None:
        build_store(self.store, profile_columns=profile_columns)

    def owner(self, live: dict | None = None) -> external.ExternalOwner:
        found = external.detect(live, home=self.root)
        assert found is not None
        return found

    def stored(self, table: str, column: str, key_column: str, key: str) -> dict:
        return stored_document(self.store, table, column, key_column, key)

    def profile(self, key: str) -> dict:
        return self.stored("providers", "settings_config", "id", key)

    def snapshot(self) -> dict:
        return self.stored("proxy_live_backup", "original_config", "app_type", "claude")

    def seed(self) -> tuple[int, tuple[str, ...]]:
        return external.apply_seed(external.plan_seed(self.owner(), HORONOM_STATUS_LINE))


class DetectTest(ExternalCase):
    def test_no_store_is_not_an_error(self) -> None:
        empty = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        self.assertIsNone(external.detect({}, home=empty))

    def test_finds_only_rows_for_this_host_tool(self) -> None:
        owner = self.owner()
        self.assertTrue(owner.supported)
        self.assertEqual(
            [item.kind for item in owner.representations],
            ["profile", "profile", "pre_takeover_snapshot"],
        )

    def test_locators_are_unique_and_name_no_provider(self) -> None:
        locators = [item.locator for item in self.owner().representations]
        self.assertEqual(len(locators), len(set(locators)))
        for locator in locators:
            self.assertNotIn("p1", locator)
            self.assertNotIn("p2", locator)

    def test_proxy_takeover_is_reported_without_echoing_the_value(self) -> None:
        owner = self.owner({"env": {"ANTHROPIC_AUTH_TOKEN": "PROXY_MANAGED"}})
        self.assertTrue(any("placeholder" in line for line in owner.evidence))
        self.assertNotIn("PROXY_MANAGED", json.dumps(owner.to_json()))

    def test_unrecognised_shape_is_reported_not_worked_around(self) -> None:
        self.store.unlink()
        self.build_store(profile_columns="id TEXT, app_type TEXT")
        owner = self.owner()
        self.assertFalse(owner.supported)
        self.assertIn("settings_config", owner.problem)
        self.assertEqual(owner.representations, ())

    def test_a_report_carries_no_stored_values(self) -> None:
        report = json.dumps(self.owner().to_json())
        for secret_shaped in ("gateway.example.invalid", "600000", "claude-opus-4"):
            self.assertNotIn(secret_shaped, report)


class SeedTest(ExternalCase):
    def test_seeding_makes_the_managers_own_write_carry_the_statusline(self) -> None:
        """The whole point: after seeding, both destructive paths preserve us."""
        self.seed()
        live = self.root / "settings.json"

        replay_over_live(live, self.snapshot())
        self.assertEqual(json.loads(live.read_text())["statusLine"], HORONOM_STATUS_LINE)

        switch_over_live(live, self.profile("p1"))
        self.assertEqual(json.loads(live.read_text())["statusLine"], HORONOM_STATUS_LINE)

        switch_over_live(live, self.profile("p2"))
        self.assertEqual(json.loads(live.read_text())["statusLine"], HORONOM_STATUS_LINE)

    def test_unseeded_control_loses_the_statusline(self) -> None:
        """Without the seed the same replay drops it, so the test above is not vacuous."""
        live = self.root / "settings.json"
        replay_over_live(live, self.snapshot())
        self.assertNotIn(external.MARKER_KEY, json.loads(live.read_text())["statusLine"])

    def test_seeding_preserves_every_other_key_exactly(self) -> None:
        before = {key: self.profile(key) for key in ("p1", "p2")}
        before["snapshot"] = self.snapshot()
        self.seed()
        after = {key: self.profile(key) for key in ("p1", "p2")}
        after["snapshot"] = self.snapshot()
        for key, document in after.items():
            with self.subTest(row=key):
                self.assertEqual(
                    {k: v for k, v in document.items() if k != "statusLine"},
                    {k: v for k, v in before[key].items() if k != "statusLine"},
                )

    def test_rows_for_another_host_tool_are_untouched(self) -> None:
        before = self.profile("p3")
        self.seed()
        self.assertEqual(self.profile("p3"), before)

    def test_seeding_is_idempotent(self) -> None:
        first, _ = self.seed()
        self.assertEqual(first, 3)
        second, locators = self.seed()
        self.assertEqual((second, locators), (0, ()))

    def test_a_row_that_moved_aborts_the_whole_seed(self) -> None:
        owner = self.owner()
        plan = external.plan_seed(owner, HORONOM_STATUS_LINE)
        # The manager writes a row between plan and apply. The snapshot row is
        # chosen because it is the *last* target: the two profile rows are written
        # before the conflict is found, so this exercises the rollback rather than
        # merely the ordering.
        self.assertEqual(plan.targets[-1].table, "proxy_live_backup")
        connection = sqlite3.connect(self.store)
        with connection:
            connection.execute(
                "UPDATE proxy_live_backup SET original_config = ? WHERE app_type = ?",
                (json.dumps(rich_document(None)), "claude"),
            )
        connection.close()

        with self.assertRaises(external.ExternalOwnerError) as caught:
            external.apply_seed(plan)
        self.assertIn("changed after this plan was formed", str(caught.exception))
        # All or nothing: the rows written before the conflict must be rolled back.
        self.assertEqual(self.profile("p1")["statusLine"], USER_STATUS_LINE)
        self.assertNotIn("statusLine", self.profile("p2"))

    def test_an_unsupported_store_refuses_rather_than_guessing(self) -> None:
        self.store.unlink()
        self.build_store(profile_columns="id TEXT, app_type TEXT")
        with self.assertRaises(external.ExternalOwnerError):
            external.apply_seed(external.plan_seed(self.owner(), HORONOM_STATUS_LINE))


class UnseedTest(ExternalCase):
    def test_unseed_puts_back_the_recorded_upstream(self) -> None:
        self.seed()
        external.apply_seed(external.plan_unseed(self.owner(), USER_STATUS_LINE))
        for key in ("p1", "p2"):
            self.assertEqual(self.profile(key)["statusLine"], USER_STATUS_LINE)
        self.assertEqual(self.snapshot()["statusLine"], USER_STATUS_LINE)

    def test_unseed_removes_the_key_when_there_was_no_upstream(self) -> None:
        self.seed()
        external.apply_seed(external.plan_unseed(self.owner(), None))
        self.assertNotIn("statusLine", self.profile("p1"))
        self.assertNotIn("statusLine", self.snapshot())

    def test_unseed_never_touches_a_statusline_that_is_not_ours(self) -> None:
        """A+B+C -> A+C: removal takes out our entry and leaves the user's alone."""
        plan = external.plan_unseed(self.owner(), None)
        self.assertEqual(plan.targets, ())
        self.assertEqual(self.profile("p1")["statusLine"], USER_STATUS_LINE)

    def test_unseed_leaves_other_keys_exactly_as_found(self) -> None:
        self.seed()
        before = self.profile("p1")
        external.apply_seed(external.plan_unseed(self.owner(), USER_STATUS_LINE))
        after = self.profile("p1")
        self.assertEqual(
            {k: v for k, v in after.items() if k != "statusLine"},
            {k: v for k, v in before.items() if k != "statusLine"},
        )


class DivergenceTest(ExternalCase):
    def test_a_drifted_copy_is_reported(self) -> None:
        self.seed()
        self.assertEqual(external.divergence(self.owner(), HORONOM_STATUS_LINE), ())

        stale = dict(HORONOM_STATUS_LINE, command="/old/path/statusline_compositor.py")
        connection = sqlite3.connect(self.store)
        with connection:
            connection.execute(
                "UPDATE providers SET settings_config = ? WHERE id = ?",
                (json.dumps(rich_document(stale)), "p1"),
            )
        connection.close()

        diverged = external.divergence(self.owner(), HORONOM_STATUS_LINE)
        self.assertEqual(len(diverged), 1)

    def test_divergence_is_what_a_reseed_fixes(self) -> None:
        self.seed()
        connection = sqlite3.connect(self.store)
        with connection:
            connection.execute(
                "UPDATE providers SET settings_config = ? WHERE id = ?",
                (json.dumps(rich_document(dict(HORONOM_STATUS_LINE, padding=9))), "p1"),
            )
        connection.close()
        self.assertTrue(external.divergence(self.owner(), HORONOM_STATUS_LINE))
        self.seed()
        self.assertEqual(external.divergence(self.owner(), HORONOM_STATUS_LINE), ())


if __name__ == "__main__":
    unittest.main()
