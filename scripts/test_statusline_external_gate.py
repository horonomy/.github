"""The external-config-manager gate: the coexistence matrix (HORO-1660).

The release gate (HORO-1572) walks the lifecycle with this product as the only
thing writing the host settings file. That assumption is false on the machine
this defect was found on: another tool manages the same file, and twice in one
day it replayed a week-old copy of it over the top, taking the statusline with
it. This gate is the same idea as that one with the assumption removed -- drive
the lifecycle *and* the other tool's own writes, in ten starting positions, and
after every step check the five things whose loss is a release-blocking defect.

Those five, checked by `ExternalGateCase.invariants` and never by inspection:

1. every field the external manager owns is still in its store, exactly as it
   last wrote it -- provider, model, base URL, credentials, and the keys a newer
   version of it has added that this code has never heard of;
2. its rows for a *different* host application are byte-identical;
3. the statusline slot has the expected owner, and the host tool can still use
   what is configured -- the command runs, through a shell, with a Claude-shaped
   payload on stdin, and its output is read;
4. nothing that lives only in the manager's store reaches the rendered line;
5. the user's own statusline script is byte- and mode-identical.

The third is the one that cannot be replaced by reading the store back. A seeded
row that parses and diffs clean still proves nothing about whether the manager's
*own* write leaves a working statusline behind, which is the entire question.

`Manager` below is the other tool's three write paths, ported from the behaviour
its source has rather than from what its interface suggests. Two of them write a
stored document over the live file without reading it first; the third reads live
and patches only its own fields. That asymmetry is the defect, so a fixture that
modelled all three as "it writes the file" would have nothing left to prove.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import unittest
import unittest.mock

import statusline_external as external
import statusline_lifecycle as lifecycle
from test_statusline_lifecycle import RenderingCase

MINE = "MY OWN LINE"

THREE = ("fornax", "circinus", "libra")
LABELS = ("VERIFIED", "SHADOW", "APPROVED")

# The four internal keys the manager strips on its way to the live file, and the
# whole of its transformation. Named here so a case can plant one and prove the
# port is faithful rather than approximate.
INTERNAL_KEYS = ("api_format", "apiFormat", "openrouter_compat_mode", "openrouterCompatMode")

# Assembled rather than written out, because a credential-shaped literal in a
# public repository is a finding in its own right even when it is a fixture.
TOKEN = "sk-" + "ant-" + "api03-" + "fixture-value-never-real"
GATEWAY_URL = "https://gateway.example.invalid/v1?trailing=true"
DIRECT_URL = "https://direct.example.invalid/v1"
PROXY_URL = "http://127.0.0.1:15721"
PROXY_PLACEHOLDER = "PROXY_MANAGED"

# Values that exist only inside the manager's store or its proxied env block.
# None of them may reach the rendered line.
NEVER_RENDERED = (TOKEN, GATEWAY_URL, DIRECT_URL, PROXY_PLACEHOLDER)


class Manager:
    """The external manager's own writes, as its source performs them.

    Every method that changes the manager's own configuration records what it
    believes its store now says, which is what lets the gate check preservation
    without the cases restating it. A test that had to re-declare the expected
    contents after each of its own steps would eventually declare them wrong.
    """

    APP = "claude"
    OTHER_APP = "codex"

    def __init__(self, store: pathlib.Path, live: pathlib.Path) -> None:
        self.store = store
        self.live = live
        self.store.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.store)
        with connection:
            connection.execute(
                "CREATE TABLE providers (id TEXT, app_type TEXT, settings_config TEXT)"
            )
            connection.execute(
                "CREATE TABLE proxy_live_backup "
                "(app_type TEXT, original_config TEXT, backed_up_at TEXT)"
            )
        connection.close()
        self._own: dict[tuple[str, str], dict] = {}

    # -- its own store ------------------------------------------------------

    def _execute(self, statement: str, parameters: tuple) -> None:
        connection = sqlite3.connect(self.store)
        with connection:
            connection.execute(statement, parameters)
        connection.close()

    def _query(self, statement: str, parameters: tuple) -> object:
        connection = sqlite3.connect(f"file:{self.store}?mode=ro", uri=True)
        try:
            row = connection.execute(statement, parameters).fetchone()
        finally:
            connection.close()
        return None if row is None else row[0]

    def _remember(self, table: str, key: str, document: dict) -> None:
        self._own[(table, key)] = {
            name: value
            for name, value in document.items()
            if name != lifecycle.STATUS_LINE_KEY
        }

    def _forget(self, table: str, key: str) -> None:
        self._own.pop((table, key), None)

    def mine(self) -> dict[tuple[str, str], dict]:
        """Every field of its own the manager expects to still be in its store."""
        return dict(self._own)

    def current(self) -> dict[tuple[str, str], dict]:
        """The same rows, minus the statusline, as the store holds them now."""
        found: dict[tuple[str, str], dict] = {}
        for table, key in self._own:
            column = "settings_config" if table == "providers" else "original_config"
            key_column = "id" if table == "providers" else "app_type"
            stored = self._query(
                f"SELECT {column} FROM {table} WHERE {key_column} = ?", (key,)
            )
            if not isinstance(stored, str):
                found[(table, key)] = {}
                continue
            document = json.loads(stored)
            found[(table, key)] = {
                name: value
                for name, value in document.items()
                if name != lifecycle.STATUS_LINE_KEY
            }
        return found

    def profile(self, name: str) -> dict:
        return json.loads(self._query("SELECT settings_config FROM providers WHERE id = ?", (name,)))

    def snapshot(self) -> dict | None:
        stored = self._query(
            "SELECT original_config FROM proxy_live_backup WHERE app_type = ?", (self.APP,)
        )
        return json.loads(stored) if isinstance(stored, str) else None

    def foreign(self) -> dict:
        """Its row for a different host application, whole."""
        return json.loads(
            self._query("SELECT settings_config FROM providers WHERE app_type = ?", (self.OTHER_APP,))
        )

    def stored_status_lines(self) -> dict[str, object]:
        """What every row for this host tool would put in the slot if written now."""
        found: dict[str, object] = {}
        for table, key in self._own:
            column = "settings_config" if table == "providers" else "original_config"
            key_column = "id" if table == "providers" else "app_type"
            stored = self._query(
                f"SELECT {column} FROM {table} WHERE {key_column} = ?", (key,)
            )
            if isinstance(stored, str):
                found[f"{table}:{key}"] = json.loads(stored).get(lifecycle.STATUS_LINE_KEY)
        return found

    # -- what a user does to it ---------------------------------------------

    def add_profile(self, name: str, captured: dict, *, model: str, base_url: str) -> None:
        """A provider the user creates: whatever was live, plus its own fields.

        That is where these documents come from -- the manager captures the live
        settings file at the moment a profile is made. Which is why a profile made
        before this product was installed carries the user's own statusline, and
        one made before they had any carries none at all.
        """
        stored = dict(captured)
        stored["model"] = model
        stored["env"] = dict(stored.get("env", {})) | {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": TOKEN,
        }
        # One of the four keys it keeps for itself and strips on the way out.
        stored["apiFormat"] = "anthropic"
        self._execute("INSERT INTO providers VALUES (?, ?, ?)", (name, self.APP, json.dumps(stored)))
        self._remember("providers", name, stored)

    def add_foreign_profile(self, captured: dict) -> None:
        stored = dict(captured) | {"model": "gpt-irrelevant"}
        self._execute(
            "INSERT INTO providers VALUES (?, ?, ?)",
            (self.OTHER_APP, self.OTHER_APP, json.dumps(stored)),
        )

    def edit_profile(
        self,
        name: str,
        *,
        model: str | None = None,
        env: dict | None = None,
        drop_env: tuple[str, ...] = (),
    ) -> None:
        """The manager changing its own configuration, in its own store."""
        stored = self.profile(name)
        if model is not None:
            stored["model"] = model
        block = dict(stored.get("env", {}))
        block.update(env or {})
        for key in drop_env:
            block.pop(key, None)
        stored["env"] = block
        self._execute(
            "UPDATE providers SET settings_config = ? WHERE id = ?", (json.dumps(stored), name)
        )
        self._remember("providers", name, stored)

    # -- its three write paths ----------------------------------------------

    def _to_live(self, document: dict) -> None:
        out = {key: value for key, value in document.items() if key not in INTERNAL_KEYS}
        self.live.write_text(json.dumps(out, indent=2) + "\n")

    def switch_to(self, name: str) -> None:
        """`write_live_snapshot`: the stored document written over live, unread."""
        self._to_live(self.profile(name))

    def recover(self) -> None:
        """A restart that finds takeover residue: the pre-takeover copy replayed.

        Including the part that makes it unrecoverable by hand -- the snapshot is
        deleted afterwards, so there is nothing left to compare the live file
        against. This is the path that actually fired, twice, on 2026-10-02.
        """
        snapshot = self.snapshot()
        assert snapshot is not None, "there is no takeover residue to recover from"
        self._to_live(snapshot)
        self._execute("DELETE FROM proxy_live_backup WHERE app_type = ?", (self.APP,))
        self._forget("proxy_live_backup", self.APP)

    def take_over(self) -> None:
        """The one path that reads the live file first, and so keeps what it does not know."""
        current = json.loads(self.live.read_text())
        # Fresh residue, never a second row: a store with two backups for one host
        # tool would make `snapshot()` answer about whichever came first, and the
        # cases that recover twice would then assert against the wrong document.
        self._execute("DELETE FROM proxy_live_backup WHERE app_type = ?", (self.APP,))
        self._execute(
            "INSERT INTO proxy_live_backup VALUES (?, ?, ?)",
            (self.APP, json.dumps(current), "2026-09-25T01:10:01Z"),
        )
        self._remember("proxy_live_backup", self.APP, current)
        patched = dict(current)
        patched["env"] = dict(patched.get("env", {})) | {
            "ANTHROPIC_BASE_URL": PROXY_URL,
            "ANTHROPIC_AUTH_TOKEN": PROXY_PLACEHOLDER,
        }
        self.live.write_text(json.dumps(patched, indent=2) + "\n")


class ExternalGateCase(RenderingCase):
    """A settings file two tools write, and the five-invariant check after each step.

    The starting position is the one the defect was found in, not a convenient
    one: the manager already holds two profiles captured at different moments --
    so one carries the user's statusline and the other carries none -- it is
    currently proxying the host tool, and its pre-takeover copy of the file
    predates this product entirely.
    """

    def setUp(self) -> None:
        super().setUp()
        self.store = self.root / ".cc-switch" / "cc-switch.db"
        self.manager = Manager(self.store, self.settings)

        # A profile made while the user had their own statusline.
        self.manager.add_profile(
            "gateway", self._read(), model="claude-opus-4", base_url=GATEWAY_URL
        )
        # One made before they had any, which is the row a seed has to *add* a key
        # to rather than replace one in.
        bare = {key: value for key, value in self._read().items() if key != lifecycle.STATUS_LINE_KEY}
        self.manager.add_profile("direct", bare, model="claude-sonnet-5", base_url=DIRECT_URL)
        self.manager.add_foreign_profile(self._read())
        self.foreign = self.manager.foreign()

        # And it is proxying right now, which is how the stale copy came to exist.
        self.manager.take_over()

        # The file as it is before this product has touched it. Kept because a
        # profile created *after* the install is the one case where a copy of
        # something pre-Horonom re-enters the store, and copying a seeded row to
        # stand in for it would make that case prove nothing.
        self.pristine = self._read()

        self.untouchable: dict[pathlib.Path, tuple[bytes, int]] = {}
        self.guard(self.upstream)

    def guard(self, path: pathlib.Path) -> None:
        self.untouchable[path] = (path.read_bytes(), path.stat().st_mode & 0o7777)

    def owner(self) -> external.ExternalOwner:
        """Detected freshly each time, because a plan is formed against one read."""
        found = external.detect(self._read(), home=self.root)
        assert found is not None
        return found

    def invariants(
        self,
        *,
        slot: lifecycle.Ownership,
        providers: tuple[str, ...],
        labels: tuple[str, ...] = (),
        upstream: bool = True,
    ) -> str:
        """The five checks this gate owes every step of every case."""
        # (1) every field the manager owns, as it last wrote it.
        self.assertEqual(self.manager.current(), self.manager.mine())

        # (2) and its rows for a host tool that is not this one, whole.
        self.assertEqual(self.manager.foreign(), self.foreign)

        # (3) who has the slot, and whether the host tool can still use it.
        current = self._read()
        self.assertIs(lifecycle.classify(lifecycle.read_settings(self.settings)), slot)
        self.assertEqual(
            lifecycle._provider_ids(lifecycle.read_registry(self.home)), providers
        )
        configured = current[lifecycle.STATUS_LINE_KEY]["command"]
        self.assertIsInstance(configured, str)
        self.assertTrue(configured.strip())
        line = self.render()
        if upstream:
            self.assertTrue(line.startswith(MINE), line)
        for label in labels:
            self.assertIn(label, line)

        # (4) nothing out of the other tool's store, in the line a user reads.
        for value in NEVER_RENDERED:
            self.assertNotIn(value, line)

        # (5) the user's own script, byte-for-byte and mode-for-mode.
        for path, (payload, mode) in self.untouchable.items():
            self.assertEqual(path.read_bytes(), payload, path)
            self.assertEqual(path.stat().st_mode & 0o7777, mode, path)
        return line

    def assert_store_carries_the_live_statusline(self) -> None:
        """Every stored copy says exactly what the live file says.

        EXTERNAL_MANAGER_CANONICAL_SEED_IS_SINGLE_SOURCED.

        One assertion rather than a count, because the failure this catches is
        divergence: copies that all carry *a* statusline but not the same one mean
        switching profile silently changes which version of the product is in the
        line.
        """
        live = self._read()[lifecycle.STATUS_LINE_KEY]
        self.assertEqual(
            self.manager.stored_status_lines(),
            {locator: live for locator in self.manager.stored_status_lines()},
        )

    # -- lifecycle steps, with the other tool declared ----------------------

    def fornax(self) -> pathlib.Path:
        return self.provider_script("fornax-provider.sh", "fornax", "VERIFIED")

    def circinus(self) -> pathlib.Path:
        return self.provider_script("circinus-provider.sh", "circinus", "SHADOW")

    def libra(self) -> pathlib.Path:
        return self.provider_script("libra-provider.sh", "libra", "APPROVED")

    def adopt(self, provider: str, script: pathlib.Path) -> lifecycle.ApplyResult:
        result = self.enable(
            provider, argv=(str(script),), timeout_ms=2000, owner=self.owner()
        )
        # Installed *and* durable are two findings, and the gate wants both said.
        self.assertTrue(result.verified)
        self.assertTrue(result.durable, result.external_problem)
        return result

    def enable_all(self) -> None:
        self.adopt("fornax", self.fornax())
        self.invariants(
            slot=lifecycle.Ownership.HORONOM_OWNED, providers=("fornax",), labels=("VERIFIED",)
        )
        self.adopt("circinus", self.circinus())
        self.adopt("libra", self.libra())
        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        # Deliberately not asserting store fidelity here. The five invariants are
        # what every step owes; whether the store is seeded is a claim particular
        # cases make, and checking it in the shared setup would mean a case about
        # what happens *after* a bad seed never got as far as its own subject.

    def uninstall(self) -> lifecycle.ApplyResult:
        document, registry = self.documents()
        return lifecycle.apply(
            lifecycle.plan_remove(
                document,
                registry,
                providers=lifecycle._provider_ids(registry),
                operation="uninstall",
                owner=self.owner(),
            )
        )


class CaseAReapplyUnrelatedProviderConfigTest(ExternalGateCase):
    def test_the_manager_reapplying_its_own_configuration_keeps_the_statusline(self) -> None:
        self.enable_all()
        self.manager.edit_profile("gateway", env={"ANTHROPIC_CUSTOM_HEADERS": "X-Tenant: dogfood"})
        self.manager.switch_to("gateway")

        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        # Its change took effect. Coexistence means both tools get what they
        # configured, not that one of them was prevented from writing.
        self.assertEqual(
            self._read()["env"]["ANTHROPIC_CUSTOM_HEADERS"], "X-Tenant: dogfood"
        )
        # And the four keys it keeps to itself did not leak into the live file.
        for key in INTERNAL_KEYS:
            self.assertNotIn(key, self._read())


class CaseBProfileSwitchTest(ExternalGateCase):
    def test_switching_between_profiles_never_changes_which_statusline_runs(self) -> None:
        self.enable_all()
        first = self.invariants(
            slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS
        )

        for profile in ("direct", "gateway", "direct"):
            with self.subTest(profile=profile):
                self.manager.switch_to(profile)
                line = self.invariants(
                    slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS
                )
                # Identical, not merely present: a statusline that differs per
                # profile is the divergence failure rather than the fix.
                self.assertEqual(line, first)

        # `direct` was captured before the user had any statusline at all, so the
        # seed had to add the key there rather than replace one.
        self.assertIn(lifecycle.STATUS_LINE_KEY, self.manager.profile("direct"))
        self.assert_store_carries_the_live_statusline()


class CaseCEnvKeysAddedAndRemovedTest(ExternalGateCase):
    def test_env_keys_the_manager_owns_move_freely_under_the_statusline(self) -> None:
        self.enable_all()
        self.manager.edit_profile(
            "gateway",
            env={"ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4-5"},
            drop_env=("DOGFOOD_ENDPOINT",),
        )
        self.manager.switch_to("gateway")

        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        env = self._read()["env"]
        self.assertEqual(env["ANTHROPIC_SMALL_FAST_MODEL"], "claude-haiku-4-5")
        self.assertNotIn("DOGFOOD_ENDPOINT", env)

    def test_the_wider_upstream_defect_is_recorded_rather_than_implied(self) -> None:
        """What seeding the statusline does not fix, asserted so the scope is a fact.

        The same unread write that took the statusline also took another product's
        env keys and the whole `hooks` block, and no amount of seeding `statusLine`
        brings those back -- they belong to products that would each have to seed
        their own. Filed as the upstream defect it is; pinned here so nobody reads
        this gate as a claim that the manager now preserves everything.
        """
        self.enable_all()
        data = self._read()
        data["env"] = dict(data["env"]) | {"CIRCINUS_DOGFOOD_PROFILE": "founder"}
        self.write(data)

        self.manager.switch_to("gateway")

        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        self.assertNotIn("CIRCINUS_DOGFOOD_PROFILE", self._read()["env"])


class CaseDModelChangedTest(ExternalGateCase):
    def test_changing_the_model_does_not_disturb_the_statusline_or_need_to(self) -> None:
        self.enable_all()
        self.manager.edit_profile("gateway", model="claude-haiku-4-5")
        self.manager.switch_to("gateway")

        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        self.assertEqual(self._read()["model"], "claude-haiku-4-5")
        # Nothing of this product's reached its model configuration either: the
        # obligation runs both ways.
        self.assertEqual(self.manager.profile("gateway")["model"], "claude-haiku-4-5")


class CaseEBaseUrlChangedTest(ExternalGateCase):
    def test_changing_the_base_url_does_not_disturb_the_statusline(self) -> None:
        self.enable_all()
        moved = "https://moved.example.invalid/v1"
        self.manager.edit_profile("gateway", env={"ANTHROPIC_BASE_URL": moved})
        self.manager.switch_to("gateway")

        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        self.assertEqual(self._read()["env"]["ANTHROPIC_BASE_URL"], moved)
        self.assertNotIn(moved, self.render())


class CaseFReloadAfterRebootTest(ExternalGateCase):
    def test_the_restart_that_replayed_a_week_old_copy_now_replays_ours(self) -> None:
        """The sequence that actually happened, twice, on 2026-10-02."""
        self.enable_all()
        installed = self._read()[lifecycle.STATUS_LINE_KEY]

        # Login: the manager finds takeover residue, decides it exited abnormally,
        # replays its pre-takeover copy of the file wholesale, and takes over again.
        self.manager.recover()
        self.manager.take_over()

        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)
        self.assertEqual(self._read()[lifecycle.STATUS_LINE_KEY], installed)
        # It is proxying again, so its own takeover survived too.
        self.assertEqual(self._read()["env"]["ANTHROPIC_AUTH_TOKEN"], PROXY_PLACEHOLDER)

    def test_without_the_seed_that_same_restart_takes_the_statusline(self) -> None:
        """The control. Without it the case above cannot tell a fix from a no-op."""
        self.enable("fornax", argv=(str(self.fornax()),), timeout_ms=2000)
        self.manager.recover()

        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)),
            lifecycle.Ownership.USER_OWNED,
        )
        # And `doctor` is the thing that would have said so in advance.
        report = lifecycle.doctor(self.settings, self.home, owner=self.owner())
        self.assertTrue(report["external_owner"]["detected"])


class CaseGEnableAfterAProfileExistsTest(ExternalGateCase):
    def test_installing_into_a_file_another_tool_already_manages(self) -> None:
        document, registry = self.documents()
        plan = lifecycle.plan_enable(
            document,
            registry,
            lifecycle.ProviderRegistration(
                provider="fornax", argv=(str(self.fornax()),), scope="host", timeout_ms=2000
            ),
            owner=self.owner(),
        )
        # Reconciled, not refused and not silently skipped: detecting another owner
        # is a reason to seed it, never a reason to install nothing.
        self.assertIsNone(plan.refusal)
        self.assertIsNotNone(plan.external)
        self.assertEqual(len(plan.external.targets), 3)

        result = lifecycle.apply(plan)
        self.assertTrue(result.durable, result.external_problem)
        self.invariants(
            slot=lifecycle.Ownership.HORONOM_OWNED, providers=("fornax",), labels=("VERIFIED",)
        )
        self.assert_store_carries_the_live_statusline()

    def test_a_profile_created_after_the_install_is_repaired_by_enabling_again(self) -> None:
        self.enable_all()
        # A provider created from a template the manager captured before the
        # install, which is the state a user really lands in: one row in the store
        # that never carried our key, and one profile switch away from the line
        # going back to what it was weeks ago.
        self.manager.add_profile(
            "third", self.pristine, model="claude-opus-4", base_url=DIRECT_URL
        )
        self.assertNotIn(lifecycle.MARKER_KEY, self.manager.profile("third")[lifecycle.STATUS_LINE_KEY])

        plan = self.plan_enable(
            "fornax", argv=(str(self.fornax()),), timeout_ms=2000, owner=self.owner()
        )
        lifecycle.apply(plan)

        self.manager.switch_to("third")
        self.invariants(slot=lifecycle.Ownership.HORONOM_OWNED, providers=THREE, labels=LABELS)


class CaseHDisableWhileTheManagerOwnsFieldsTest(ExternalGateCase):
    def test_removing_one_product_leaves_the_seed_and_the_managers_fields_alone(self) -> None:
        self.enable_all()
        document, registry = self.documents()
        plan = lifecycle.plan_remove(
            document, registry, providers=("circinus",), operation="disable", owner=self.owner()
        )
        # The statusline stays on the line while any product remains, so there is
        # nothing to hand back -- and a removal that unseeded here would leave the
        # remaining two one profile switch from vanishing.
        self.assertIsNone(plan.external)
        lifecycle.apply(plan)

        line = self.invariants(
            slot=lifecycle.Ownership.HORONOM_OWNED,
            providers=("fornax", "libra"),
            labels=("VERIFIED", "APPROVED"),
        )
        self.assertNotIn("SHADOW", line)
        self.manager.switch_to("direct")
        self.invariants(
            slot=lifecycle.Ownership.HORONOM_OWNED,
            providers=("fornax", "libra"),
            labels=("VERIFIED", "APPROVED"),
        )


class CaseIUninstallTest(ExternalGateCase):
    def test_uninstalling_hands_the_slot_back_on_both_sides(self) -> None:
        self.enable_all()
        result = self.uninstall()

        self.assertTrue(result.durable, result.external_problem)
        self.invariants(slot=lifecycle.Ownership.USER_OWNED, providers=())
        self.assertEqual(
            self._read()[lifecycle.STATUS_LINE_KEY]["command"], str(self.upstream)
        )
        # Nothing of ours left in the other tool's store to be reinstalled by it.
        self.assert_store_carries_the_live_statusline()

    def test_a_later_user_edit_survives_the_uninstall(self) -> None:
        self.enable_all()
        data = self._read()
        data["cleanupPeriodDays"] = 90
        self.write(data)

        self.uninstall()

        self.assertEqual(self._read()["cleanupPeriodDays"], 90)
        self.assertEqual(self._read()["env"]["ANTHROPIC_AUTH_TOKEN"], PROXY_PLACEHOLDER)


class CaseJManagerApplyAfterUninstallTest(ExternalGateCase):
    def test_neither_of_the_managers_writes_brings_the_compositor_back(self) -> None:
        """No overwrite loop and no resurrection: once removed, it stays removed."""
        self.enable_all()
        removed = self._read()
        self.uninstall()
        expected = self._read()[lifecycle.STATUS_LINE_KEY]

        for step in ("switch", "recover"):
            with self.subTest(step=step):
                if step == "switch":
                    self.manager.switch_to("gateway")
                else:
                    self.manager.take_over()
                    self.manager.recover()
                self.invariants(slot=lifecycle.Ownership.USER_OWNED, providers=())
                self.assertEqual(self._read()[lifecycle.STATUS_LINE_KEY], expected)
                self.assertNotIn(
                    lifecycle.MARKER_KEY, self._read()[lifecycle.STATUS_LINE_KEY]
                )

        self.assertNotEqual(removed[lifecycle.STATUS_LINE_KEY], expected)

    def test_a_seed_left_behind_is_what_doctor_calls_it(self) -> None:
        # The state after an uninstall that could not reach the store: the other
        # tool would put the compositor back, and nothing else would say so.
        self.enable_all()
        document, registry = self.documents()
        lifecycle.apply(
            lifecycle.plan_remove(
                document,
                registry,
                providers=lifecycle._provider_ids(registry),
                operation="uninstall",
            )
        )
        section = lifecycle.doctor(self.settings, self.home, owner=self.owner())["external_owner"]

        self.assertTrue(section["stale_seed"])
        self.manager.switch_to("gateway")
        # Which is exactly what happens if it is left alone.
        self.assertIn(lifecycle.MARKER_KEY, self._read()[lifecycle.STATUS_LINE_KEY])


class WriteDisciplineTest(ExternalGateCase):
    def test_an_operation_writes_the_host_file_at_most_once(self) -> None:
        """No polling, no watcher, no re-asserting: one write per operation.

        EXTERNAL_MANAGER_IS_DETECTED_NOT_FOUGHT, in the form the contract says
        cannot be proven from an end state -- only by counting writes.

        The failure mode this forbids is the one that looks like a fix and is not
        -- two tools each restoring what the other just wrote. A fix that needed to
        write twice to win would show up here first.
        """
        counted: list[pathlib.Path] = []
        real = lifecycle.atomic_write

        def counting(path, payload, **kwargs):
            counted.append(path)
            return real(path, payload, **kwargs)

        with unittest.mock.patch.object(lifecycle, "atomic_write", counting):
            self.adopt("fornax", self.fornax())
            self.assertEqual(counted.count(self.settings), 1)
            counted.clear()

            self.manager.switch_to("gateway")
            # The manager wrote. Nothing of ours reacts to that, because nothing of
            # ours is watching -- the seed is what makes watching unnecessary.
            self.assertEqual(counted, [])

            self.uninstall()
            self.assertEqual(counted.count(self.settings), 1)

    def test_nothing_is_written_at_all_when_both_sides_already_agree(self) -> None:
        self.adopt("fornax", self.fornax())
        before = (self.settings.read_bytes(), self.manager.profile("gateway"))

        plan = self.plan_enable(
            "fornax", argv=(str(self.fornax()),), timeout_ms=2000, owner=self.owner()
        )
        self.assertFalse(plan.mutates, plan.to_json())
        result = lifecycle.apply(plan)

        self.assertFalse(result.settings_written)
        self.assertEqual(result.external_written, ())
        self.assertEqual((self.settings.read_bytes(), self.manager.profile("gateway")), before)


if __name__ == "__main__":
    unittest.main()
