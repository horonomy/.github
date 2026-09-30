"""Tests for the statusline install lifecycle.

Structured around the 14 named properties in
`governance/product/host-config-ownership-test-contract.md`. Each property ID
appears in a comment next to the assertion that actually proves it, which is the
form that document asks for -- an ID mentioned only in a docstring is asserted,
not proven.

Every fixture here is deliberately rich. That contract's own words: "A fixture
built from `{}` proves nothing -- none of the 14 properties above are
distinguishable from a no-op against an empty file." So the settings file used
throughout carries scalars of three types, a nested object, a list holding
entries from two different owners, an MDM-shaped path, an unknown future field at
both the top level and inside `statusLine`, and env-var- and URL-shaped values
that a careless re-serialisation would mangle.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_lifecycle as lifecycle
import statusline_render as render


def rich_settings() -> dict:
    """A settings file with something of every shape that could be damaged.

    Returned fresh each call because tests mutate it. The `_circinus` entry and
    the unmarked third-party hook next to it are what make
    `OTHER_PRODUCT_CONFIG_IS_PRESERVED` a real assertion rather than a tautology:
    one of them has an ownership marker and the other does not, and neither is
    ours.
    """
    return {
        "model": "claude-opus-4",
        "theme": "dark-daltonized",
        "cleanupPeriodDays": 30,
        "skipWorkflowUsageWarning": True,
        "env": {
            "PATH_EXTRA": "/opt/homebrew/bin:/usr/local/bin",
            "DOGFOOD_ENDPOINT": "https://api.example.invalid/v1?trailing=true",
        },
        "permissions": {"allow": ["Bash(git status)", "Read(//tmp/**)"], "deny": []},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/opt/circinus/bin/hook",
                            "_circinus": "0.4.1",
                        }
                    ],
                },
                {
                    "matcher": "Edit",
                    "hooks": [{"type": "command", "command": "/usr/local/bin/vendor-hook"}],
                },
            ]
        },
        "managedSettingsSource": "/Library/Application Support/ClaudeCode/managed-settings.json",
        "futureUnknownKey": {"introducedIn": "a release this code predates", "keep": True},
        "statusLine": {
            "type": "command",
            "command": "/Users/founder/.claude/statusline-dogfood.sh",
            "padding": 1,
            "refreshInterval": 3,
            "futureUnknownKey": "keep-me",
        },
    }


def unowned(data: dict) -> dict:
    """Everything in a settings document that this module must never touch."""
    return {key: value for key, value in data.items() if key != lifecycle.STATUS_LINE_KEY}


class LifecycleCase(unittest.TestCase):
    """Base fixture: a rich settings file, and helpers that drive real operations.

    The helpers deliberately go through `plan_*` and `apply` rather than a
    shortcut, so that what the tests exercise is the same path the CLI takes. A
    test that reaches past the planner would prove the planner's guards are
    unnecessary rather than that they work.
    """

    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.settings = self.root / ".claude" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.home = self.root / "state"
        self.original = rich_settings()
        self.write(self.original)

    def deny_reads(self, target: pathlib.Path) -> object:
        """Make exactly one path unreadable.

        Monkeypatched rather than chmod-ed, because a suite running as root
        would read the file anyway and the test would pass without ever
        reaching the guard. One path at a time, so a test about the settings
        file cannot be satisfied by the registry failing instead.
        """
        real = pathlib.Path.read_bytes

        def fake(this: pathlib.Path) -> bytes:
            if this == target:
                raise PermissionError(13, "Permission denied: '/Users/founder/private/x'")
            return real(this)

        return unittest.mock.patch.object(pathlib.Path, "read_bytes", fake)

    def write(self, data: dict, *, indent: int | None = 2) -> None:
        self.settings.write_bytes(lifecycle.serialize(data, indent=indent))

    def _read(self) -> dict:
        return json.loads(self.settings.read_text())

    def _registry(self) -> dict | None:
        return lifecycle.read_registry(self.home).data

    def documents(self) -> tuple[lifecycle.SettingsDocument, lifecycle.RegistryDocument]:
        return lifecycle.read_settings(self.settings), lifecycle.read_registry(self.home)

    def plan_enable(self, provider: str, **kwargs) -> lifecycle.Plan:
        document, registry = self.documents()
        registration = lifecycle.ProviderRegistration(
            provider=provider,
            argv=kwargs.pop("argv", ("/bin/echo", provider)),
            scope=kwargs.pop("scope", "host"),
            timeout_ms=kwargs.pop("timeout_ms", None),
            explain_argv=kwargs.pop("explain_argv", None),
        )
        return lifecycle.plan_enable(document, registry, registration, **kwargs)

    def enable(self, provider: str, **kwargs) -> lifecycle.ApplyResult:
        return lifecycle.apply(self.plan_enable(provider, **kwargs))

    def plan_remove(self, *providers: str, operation: str = "disable") -> lifecycle.Plan:
        document, registry = self.documents()
        return lifecycle.plan_remove(document, registry, providers=providers, operation=operation)

    def remove(self, *providers: str, operation: str = "disable") -> lifecycle.ApplyResult:
        return lifecycle.apply(self.plan_remove(*providers, operation=operation))

    def _uninstall(self) -> lifecycle.ApplyResult:
        document, registry = self.documents()
        plan = lifecycle.plan_remove(
            document,
            registry,
            providers=lifecycle._provider_ids(registry),
            operation="uninstall",
        )
        return lifecycle.apply(plan)


class ParseSettingsTest(unittest.TestCase):
    def test_absent_and_blank_files_are_an_empty_configuration(self) -> None:
        for raw in (b"", b"   \n\t\n"):
            with self.subTest(raw=raw):
                self.assertEqual(lifecycle.parse_settings(raw), {})

    def test_truncated_json_is_refused(self) -> None:
        with self.assertRaises(lifecycle.SettingsParseError):
            lifecycle.parse_settings(b'{"model": "claude-opus-4",')

    def test_a_top_level_array_is_refused(self) -> None:
        with self.assertRaises(lifecycle.SettingsParseError):
            lifecycle.parse_settings(b'["not", "a", "settings", "object"]')

    def test_the_refusal_names_a_position_and_not_the_offending_text(self) -> None:
        # A syntax error next to a credential must not quote it back. The position
        # is enough for the user to find the line themselves.
        raw = b'{"env": {"SOME_TOKEN": "xoxb-not-a-real-secret" "missing": "comma"}}'
        with self.assertRaises(lifecycle.SettingsParseError) as caught:
            lifecycle.parse_settings(raw)
        self.assertNotIn("xoxb", str(caught.exception))
        self.assertIn("column", str(caught.exception))


class FormatFidelityTest(unittest.TestCase):
    """Re-serialisation must not restyle a file this module mostly does not own.

    Not one of the 14 named properties, but the thing that makes several of them
    checkable in practice: if every write reflows the whole document, "we changed
    one key" stops being a claim anyone can verify by looking.
    """

    def test_indentation_is_recovered_from_the_document(self) -> None:
        for raw, expected in (
            (b'{\n  "a": 1\n}\n', 2),
            (b'{\n    "a": 1\n}\n', 4),
            (b'{\n\t"a": 1\n}\n', "\t"),
            (b'{"a": 1}', None),
            (b"", lifecycle.DEFAULT_INDENT),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(lifecycle.detect_indent(raw), expected)

    def test_indentation_inside_a_string_value_is_not_mistaken_for_the_documents(self) -> None:
        raw = b'{"note": "a value with\\n    four leading spaces", "b": 2}'
        self.assertIsNone(lifecycle.detect_indent(raw))

    @staticmethod
    def round_trip(raw: bytes) -> bytes:
        document = lifecycle.SettingsDocument(
            path=pathlib.Path("settings.json"),
            raw=raw,
            data=lifecycle.parse_settings(raw),
            indent=lifecycle.detect_indent(raw),
            trailing_newline=raw.endswith(b"\n"),
            mode=0o644,
        )
        return lifecycle.serialize(
            document.data, indent=document.indent, trailing_newline=document.trailing_newline
        )

    def test_uniformly_formatted_documents_round_trip_byte_identically(self) -> None:
        for raw in (
            b'{\n  "a": 1,\n  "b": {\n    "c": 2\n  }\n}\n',
            b'{\n    "a": 1\n}\n',
            b'{\n\t"a": 1\n}\n',
            b'{"a":1,"b":2}',
            b'{"a":1}\n',
            b'{\n  "a": 1\n}',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.round_trip(raw), raw)

    def test_the_known_limits_of_reserialising_are_cosmetic_and_lossless(self) -> None:
        """What this module does *not* promise, stated so nobody reads more into it.

        `json.dump` renders one document in one style, so two things a hand-edited
        file can carry do not survive: a node inlined inside an otherwise indented
        document, and the space after a colon in a minified one. Both are checked
        here as facts rather than left to be discovered, and both are asserted to
        be cosmetic -- the parsed value is what must be identical, and is.
        """
        for raw in (b'{\n  "a": 1,\n  "b": {"c": 2}\n}\n', b'{"a": 1}\n'):
            with self.subTest(raw=raw):
                rendered = self.round_trip(raw)
                self.assertNotEqual(rendered, raw)
                self.assertEqual(json.loads(rendered), json.loads(raw))

    def test_non_ascii_values_are_not_escaped_into_unreadability(self) -> None:
        rendered = lifecycle.serialize({"theme": "tokyonight-storm ⛩"})
        self.assertIn("⛩", rendered.decode("utf-8"))


class ClassifyTest(LifecycleCase):
    def test_a_missing_key_is_absent_and_a_users_command_is_theirs(self) -> None:
        document = lifecycle.read_settings(self.settings)
        self.assertIs(lifecycle.classify(document), lifecycle.Ownership.USER_OWNED)

        data = rich_settings()
        del data[lifecycle.STATUS_LINE_KEY]
        self.write(data)
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)), lifecycle.Ownership.ABSENT
        )

    def test_shapes_this_version_does_not_understand_are_refused_not_guessed(self) -> None:
        # Each of these is cheap to refuse and expensive to guess at. `null` and an
        # unrecognised `type` in particular are how a future Claude Code feature
        # would look to today's code.
        for status_line in (
            None,
            "a bare command string",
            ["command"],
            {"type": "somethingNewInClaudeCode", "command": "/bin/true"},
            {"type": "command"},
            {"type": "command", "command": ""},
            {"type": "command", "command": 42},
            {"type": "command", "command": "/bin/true", lifecycle.MARKER_KEY: {"owner": "someone-else"}},
        ):
            with self.subTest(status_line=status_line):
                data = rich_settings()
                data[lifecycle.STATUS_LINE_KEY] = status_line
                self.write(data)
                self.assertIs(
                    lifecycle.classify(lifecycle.read_settings(self.settings)),
                    lifecycle.Ownership.UNSUPPORTED_SHAPE,
                )

    def test_our_command_without_our_marker_is_adoptable_not_owned(self) -> None:
        # The distinction that stops an install from claiming a configuration it
        # cannot prove it wrote.
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY] = {
            "type": "command",
            "command": lifecycle.compositor_command(),
        }
        self.write(data)
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)), lifecycle.Ownership.ADOPTABLE
        )

    def test_our_marker_without_our_command_is_drift(self) -> None:
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY] = {
            "type": "command",
            "command": "/Users/founder/.claude/something-else.sh",
            lifecycle.MARKER_KEY: {"owner": lifecycle.MARKER_OWNER, "version": 1},
        }
        self.write(data)
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)), lifecycle.Ownership.DRIFTED
        )


class RefersToCompositorTest(unittest.TestCase):
    def test_the_command_this_module_installs_is_recognised(self) -> None:
        self.assertTrue(lifecycle.refers_to_compositor(lifecycle.compositor_command()))

    def test_a_relocated_or_symlinked_path_still_identifies_as_ours(self) -> None:
        # Identity is by inode, so a moved checkout does not read as somebody
        # else's command -- which would leave the slot impossible to release.
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        link = root / "compositor-link.py"
        link.symlink_to(lifecycle.compositor_path())
        self.assertTrue(lifecycle.refers_to_compositor(f"/usr/bin/env python3 {link}"))

    def test_other_commands_and_unparseable_ones_are_not_ours(self) -> None:
        for command in (
            "/Users/founder/.claude/statusline-dogfood.sh",
            "",
            42,
            None,
            "'unbalanced quoting",
        ):
            with self.subTest(command=command):
                self.assertFalse(lifecycle.refers_to_compositor(command))


class SettingsScopeTest(unittest.TestCase):
    def test_scope_is_labelled_by_where_the_file_lives(self) -> None:
        for path, expected in (
            (pathlib.Path.home() / ".claude" / "settings.json", "user"),
            (pathlib.Path("/work/repo/.claude/settings.json"), "project"),
            (pathlib.Path("/tmp/somewhere/settings.json"), "other"),
        ):
            with self.subTest(path=path):
                self.assertEqual(lifecycle.settings_scope(path), expected)


class PlanShapeTest(unittest.TestCase):
    """The invariant tying a plan's three settings fields together.

    Asserted structurally rather than through an operation because the dangerous
    combination is one no current caller produces: the guard exists so that a
    future one cannot introduce a settings write with no staleness check.
    """

    def plan(self, **kwargs) -> lifecycle.Plan:
        fields = {
            "operation": "presentation",
            "settings_path": None,
            "ownership": None,
            "changes": (),
            "fingerprint": None,
        }
        return lifecycle.Plan(**{**fields, **kwargs})

    def test_a_plan_may_omit_the_settings_file_entirely(self) -> None:
        plan = self.plan()
        self.assertIsNone(plan.to_json()["settings_path"])
        self.assertIsNone(plan.to_json()["current_owner"])

    def test_omitting_the_settings_file_is_disclosed_as_host_wide(self) -> None:
        # The registry is one directory per machine, so a plan that writes only
        # the registry reaches every project -- a wider blast radius than the
        # "project" a settings path would have reported.
        self.assertEqual(self.plan().scope, "host")

    def test_the_three_settings_fields_cannot_be_supplied_apart(self) -> None:
        for partial in (
            {"settings_path": pathlib.Path("/work/repo/.claude/settings.json")},
            {"ownership": lifecycle.Ownership.ABSENT},
            {"fingerprint": "deadbeef"},
        ):
            with self.subTest(partial=sorted(partial)), self.assertRaises(ValueError):
                self.plan(**partial)

    def test_a_settings_write_cannot_be_planned_without_a_fingerprint(self) -> None:
        # The combination that would skip the staleness check in `apply`.
        with self.assertRaises(ValueError):
            self.plan(settings_after={"statusLine": {}})


class EnablePreservationTest(LifecycleCase):
    """What enabling a provider must leave exactly as it found it."""

    def test_every_key_this_module_does_not_own_survives_enabling(self) -> None:
        before = self._read()
        self.enable("fornax")
        after = self._read()
        # PREEXISTING_UNOWNED_CONFIG_IS_PRESERVED -- compared structurally across
        # the whole document, so a nested value silently dropped or retyped fails
        # here rather than passing a spot check on the keys someone thought of.
        self.assertEqual(unowned(after), unowned(before))
        self.assertEqual(set(after), set(before))

    def test_fields_this_versions_schema_has_never_seen_survive(self) -> None:
        self.enable("fornax")
        after = self._read()
        # UNKNOWN_FUTURE_FIELDS_ARE_PRESERVED -- checked at both levels, because
        # the dangerous one is inside `statusLine`: that object is the one this
        # module rewrites, so a reduce-to-known-fields bug would only show there.
        self.assertEqual(after["futureUnknownKey"], {"introducedIn": "a release this code predates", "keep": True})
        self.assertEqual(after["statusLine"]["futureUnknownKey"], "keep-me")
        self.assertEqual(after["statusLine"]["padding"], 1)
        self.assertEqual(after["statusLine"]["refreshInterval"], 3)

    def test_another_products_entries_survive_marked_or_not(self) -> None:
        before = self._read()["hooks"]
        self.enable("fornax")
        # OTHER_PRODUCT_CONFIG_IS_PRESERVED -- the marked Circinus hook and the
        # unmarked third-party one next to it both survive, which is what makes
        # this an ownership test rather than a marker-matching test.
        self.assertEqual(self._read()["hooks"], before)
        serialised = self.settings.read_text()
        self.assertIn("_circinus", serialised)
        self.assertIn("/usr/local/bin/vendor-hook", serialised)

    def test_the_mdm_shaped_value_is_never_touched(self) -> None:
        self.enable("fornax")
        # Organization/MDM-managed state untouched, in HORO-1000's vocabulary. This
        # module has no reason to read it and no path that writes it; the assertion
        # exists so that stays true.
        self.assertEqual(
            self._read()["managedSettingsSource"],
            "/Library/Application Support/ClaudeCode/managed-settings.json",
        )

    def test_the_users_command_becomes_the_registered_upstream(self) -> None:
        original = self._read()["statusLine"]["command"]
        self.enable("fornax")
        self.assertEqual(self._registry()["upstream"], {"command": original})
        self.assertTrue(lifecycle.refers_to_compositor(self._read()["statusLine"]["command"]))
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)),
            lifecycle.Ownership.HORONOM_OWNED,
        )

    def test_the_worked_example_from_the_ticket_end_to_end(self) -> None:
        """HORO-1566's own worked example, asserted item by item.

        Kept as its own test rather than folded into the others because it is the
        thing a reviewer reads to decide whether this works: a user with a rich
        custom statusline enables Fornax and loses nothing.
        """
        self.write(
            {
                "statusLine": {
                    "type": "command",
                    "command": "/my/custom/statusline",
                    "padding": 1,
                    "refreshInterval": 3,
                    "futureUnknownKey": "keep-me",
                }
            }
        )
        self.enable("fornax")
        status_line = self._read()["statusLine"]
        self.assertEqual(status_line["padding"], 1)
        self.assertEqual(status_line["refreshInterval"], 3)
        self.assertEqual(status_line["futureUnknownKey"], "keep-me")
        self.assertEqual(status_line["type"], "command")
        self.assertEqual(self._registry()["upstream"]["command"], "/my/custom/statusline")
        self.assertEqual(
            status_line[lifecycle.MARKER_KEY],
            {"owner": lifecycle.MARKER_OWNER, "version": lifecycle.MARKER_VERSION},
        )
        # Only the routing changed. Everything else in the object is what it was.
        self.assertEqual(
            set(status_line) - {lifecycle.MARKER_KEY},
            {"type", "command", "padding", "refreshInterval", "futureUnknownKey"},
        )

    def test_the_users_statusline_script_is_neither_read_nor_written(self) -> None:
        script = self.root / "statusline-dogfood.sh"
        script.write_text('#!/bin/sh\necho "the user wrote this"\n')
        script.chmod(0o755)
        before = script.read_bytes()
        stamp = script.stat()

        data = rich_settings()
        data["statusLine"]["command"] = str(script)
        self.write(data)
        self.enable("fornax")
        self.enable("circinus")
        self._uninstall()

        self.assertEqual(script.read_bytes(), before)
        self.assertEqual(script.stat().st_mtime_ns, stamp.st_mtime_ns)
        self.assertEqual(script.stat().st_mode, stamp.st_mode)

    def test_file_permissions_are_preserved_and_new_state_is_owner_only(self) -> None:
        self.settings.chmod(0o644)
        self.enable("fornax")
        # The host's own file keeps the mode the host chose. Ours does not inherit
        # it: the registry records what the user's statusline used to be, and that
        # has no reason to be world-readable.
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o644)
        registry_path = lifecycle.read_registry(self.home).path
        self.assertEqual(registry_path.stat().st_mode & 0o777, lifecycle.STATE_FILE_MODE)
        self.assertEqual(registry_path.parent.stat().st_mode & 0o777, lifecycle.STATE_DIR_MODE)

    def test_a_settings_file_we_create_is_owner_only(self) -> None:
        self.settings.unlink()
        self.enable("fornax")
        self.assertEqual(self.settings.stat().st_mode & 0o777, lifecycle.NEW_FILE_MODE)

    def test_the_plan_discloses_what_it_changes_and_what_it_leaves(self) -> None:
        self.enable("fornax")
        plan = self.plan_enable("circinus").to_json()
        categories = {change["category"] for change in plan["changes"]}
        # HORO-1000's disclosure categories have to be distinguishable in the
        # output, not merged into one "configuration updated" line.
        self.assertIn("product_owned_add", categories)
        self.assertIn("host_user_state_preserved", categories)
        self.assertIn("other_product_state_preserved", categories)
        self.assertEqual(plan["scope"], "project")
        self.assertEqual(plan["ownership_model"], "shared_artifact")
        self.assertFalse(plan["requires_os_authorization"])
        self.assertTrue(plan["would_mutate"])

    def test_a_plan_never_reproduces_a_value_from_the_settings_file(self) -> None:
        # Assembled at run time so the literal never sits in the repository, and
        # so push protection has nothing to match on.
        secret = "sk-" + "ant" + "-not-a-real-key-" + "0" * 24
        data = rich_settings()
        data["env"]["ANTHROPIC_API_KEY"] = secret
        data["statusLine"]["command"] = "/Users/founder/.claude/private/statusline.sh"
        self.write(data)

        rendered = json.dumps(self.plan_enable("fornax").to_json())
        self.assertNotIn(secret, rendered)
        self.assertNotIn("sk-", rendered)
        self.assertNotIn("/Users/founder/.claude/private", rendered)
        # The key *names* are disclosure, and are allowed. The values are not.
        self.assertIn("env", rendered)


class IdempotenceTest(LifecycleCase):
    def test_enabling_the_same_provider_twice_changes_nothing_the_second_time(self) -> None:
        self.enable("fornax")
        settings_before = self.settings.read_bytes()
        registry_file = lifecycle.read_registry(self.home).path
        registry_before = registry_file.read_bytes()

        result = self.enable("fornax")

        # REPEATED_INSTALL_IS_IDEMPOTENT -- asserted on bytes, and on the plan
        # having decided there was nothing to write at all. Byte equality alone
        # would also pass for an implementation that rewrites an identical file,
        # which is a write to a shared artifact for no reason.
        self.assertFalse(result.settings_written)
        self.assertFalse(result.registry_written)
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(registry_file.read_bytes(), registry_before)

    def test_re_enabling_does_not_duplicate_or_reorder_the_registry(self) -> None:
        self.enable("fornax")
        self.enable("circinus")
        self.enable("libra")
        order = [entry["provider"] for entry in self._registry()["providers"]]

        self.enable("circinus", timeout_ms=400)

        entries = self._registry()["providers"]
        # The middle entry is replaced in place. A remove-then-append would pass a
        # "no duplicates" check and still rewrite the document for no reason.
        self.assertEqual([entry["provider"] for entry in entries], order)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[1]["timeout_ms"], 400)

    def test_the_order_products_are_enabled_in_does_not_change_the_result(self) -> None:
        self.enable("fornax")
        self.enable("circinus")
        self.enable("libra")
        forwards = self.settings.read_bytes()
        forwards_upstream = self._registry()["upstream"]
        forwards_providers = {entry["provider"] for entry in self._registry()["providers"]}

        shutil.rmtree(self.home)
        self.write(self.original)
        self.enable("libra")
        self.enable("circinus")
        self.enable("fornax")

        # AC 4: enabling in either order leaves the same configuration. The
        # registry's own ordering is allowed to differ -- rendering order is the
        # contract's `order_hint`, not the order entries happen to sit in.
        self.assertEqual(self.settings.read_bytes(), forwards)
        self.assertEqual(self._registry()["upstream"], forwards_upstream)
        self.assertEqual(
            {entry["provider"] for entry in self._registry()["providers"]}, forwards_providers
        )


class PostInstallUserChangeTest(LifecycleCase):
    """A user who edits their configuration after installing must not lose the edit."""

    def edit_after_install(self) -> None:
        data = self._read()
        data["statusLine"]["padding"] = 4
        data["theme"] = "light-daltonized"
        data["aNewKeyTheUserAdded"] = ["after", "installing"]
        self.write(data)

    def test_edits_survive_re_running_enable_as_a_repair(self) -> None:
        self.enable("fornax")
        self.edit_after_install()
        self.enable("fornax")

        after = self._read()
        # POST_INSTALL_USER_CHANGES_SURVIVE_REPAIR -- the edit inside `statusLine`
        # is the one that matters, since that is the object a repair rewrites.
        self.assertEqual(after["statusLine"]["padding"], 4)
        self.assertEqual(after["theme"], "light-daltonized")
        self.assertEqual(after["aNewKeyTheUserAdded"], ["after", "installing"])

    def test_edits_survive_an_upgrade_that_moves_the_compositor(self) -> None:
        """An upgrade reconciles the live file; it does not re-derive it.

        The stale install is simulated the way a real one happens: the recorded
        command still resolves to the compositor, but by a different path. That is
        what a moved checkout or a reinstalled interpreter looks like, and it is
        the case where re-deriving from a template would quietly discard the user's
        `padding`.
        """
        self.enable("fornax")
        self.edit_after_install()

        link = self.root / "compositor-as-it-used-to-be.py"
        link.symlink_to(lifecycle.compositor_path())
        stale = self._read()
        stale["statusLine"]["command"] = f"/usr/bin/env python3 {link}"
        self.write(stale)
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)),
            lifecycle.Ownership.HORONOM_OWNED,
        )

        self.enable("circinus")

        after = self._read()
        # POST_INSTALL_USER_CHANGES_SURVIVE_UPGRADE
        self.assertEqual(after["statusLine"]["padding"], 4)
        self.assertEqual(after["theme"], "light-daltonized")
        self.assertEqual(after["aNewKeyTheUserAdded"], ["after", "installing"])
        self.assertEqual(after["statusLine"]["command"], lifecycle.compositor_command())
        # And the original command recorded before the upgrade is still recorded.
        self.assertEqual(
            self._registry()["upstream"]["command"],
            self.original["statusLine"]["command"],
        )


class DisableTest(LifecycleCase):
    def test_disabling_one_provider_leaves_the_others_and_the_settings_alone(self) -> None:
        self.enable("fornax")
        self.enable("circinus")
        self.enable("libra")
        settings_before = self.settings.read_bytes()

        result = self.remove("circinus")

        # AC 7, and REMOVE_TOUCHES_ONLY_PRODUCT_OWNED_STATE at provider
        # granularity: the slot is still ours, so the settings file is not
        # written at all.
        self.assertFalse(result.settings_written)
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(
            [entry["provider"] for entry in self._registry()["providers"]], ["fornax", "libra"]
        )
        self.assertEqual(self._registry()["upstream"]["command"], self.original["statusLine"]["command"])

    def test_disabling_a_provider_that_is_not_registered_is_a_reported_no_op(self) -> None:
        self.enable("fornax")
        self.enable("circinus")
        self.remove("circinus")
        registry_before = lifecycle.read_registry(self.home).path.read_bytes()
        settings_before = self.settings.read_bytes()

        plan = self.plan_remove("circinus")
        result = lifecycle.apply(plan)

        # AC 6: idempotent, and *reported* as such rather than silently succeeding
        # or falling back to cleaning up whatever it can find.
        self.assertFalse(plan.mutates)
        self.assertFalse(result.settings_written)
        self.assertFalse(result.registry_written)
        self.assertIn("circinus is not registered; nothing to remove", plan.notes)
        self.assertEqual(lifecycle.read_registry(self.home).path.read_bytes(), registry_before)
        self.assertEqual(self.settings.read_bytes(), settings_before)


class PresentationTest(LifecycleCase):
    """The reader's own rendering preference, which lives in our registry.

    Two properties carry the weight here. It must survive every other operation,
    since a preference that is quietly reset by the next `enable` is not a
    preference; and the value written must be one the compositor honours, which
    the compositor's deliberate fall-back on an unrecognised mode would otherwise
    hide behind a line that renders perfectly in the wrong style.
    """

    def plan_presentation(self, **kwargs) -> lifecycle.Plan:
        return lifecycle.plan_presentation(lifecycle.read_registry(self.home), **kwargs)

    def presentation(self, **kwargs) -> lifecycle.ApplyResult:
        return lifecycle.apply(self.plan_presentation(**kwargs))

    def stored(self) -> dict:
        return self._registry()[lifecycle.PRESENTATION_KEY]

    def effective(self) -> render.PresentationMode:
        """The mode the compositor will actually render in."""
        return compositor.load_registry(lifecycle.read_registry(self.home).path).mode

    def test_every_preference_this_can_write_is_one_the_compositor_honours(self) -> None:
        self.enable("fornax")
        for compact in (False, True):
            for glyphs in (False, True):
                with self.subTest(compact=compact, glyphs=glyphs):
                    self.presentation(compact=compact, glyphs=glyphs)
                    # Read back through the compositor's own parser, not ours. It
                    # falls back to the default on an unrecognised mode name
                    # rather than failing, so a value only this module understands
                    # would produce a line that renders correctly in the style the
                    # user did not ask for -- and nothing would report it.
                    self.assertEqual(
                        (self.effective().is_compact, self.effective().uses_glyphs),
                        (compact, glyphs),
                    )

    def test_either_axis_can_be_set_without_stating_the_other(self) -> None:
        self.enable("fornax")
        self.presentation(glyphs=False)
        self.assertFalse(self.effective().uses_glyphs)
        self.assertFalse(self.effective().is_compact)

        # The terminal got narrower. The font did not get better.
        self.presentation(compact=True)
        self.assertTrue(self.effective().is_compact)
        self.assertFalse(self.effective().uses_glyphs)

    def test_other_keys_in_the_preference_object_are_left_alone(self) -> None:
        self.enable("fornax")
        registry = self._registry()
        registry[lifecycle.PRESENTATION_KEY] = {
            "width_budget": 96,
            "futureUnknownKey": "keep-me",
        }
        lifecycle.read_registry(self.home).path.write_bytes(lifecycle.serialize(registry))

        self.presentation(glyphs=False)

        # A preference object is ours as a whole, but `mode` is the only key in it
        # this operation owns -- the same rule the settings file gets.
        self.assertEqual(self.stored()["width_budget"], 96)
        self.assertEqual(self.stored()["futureUnknownKey"], "keep-me")
        self.assertEqual(self.stored()["mode"], render.PresentationMode.PLAIN.value)

    def test_setting_a_preference_that_is_already_set_writes_nothing(self) -> None:
        self.enable("fornax")
        self.presentation(compact=True, glyphs=False)
        before = lifecycle.read_registry(self.home).path.read_bytes()

        plan = self.plan_presentation(compact=True, glyphs=False)
        result = lifecycle.apply(plan)

        self.assertFalse(plan.mutates)
        self.assertFalse(result.registry_written)
        self.assertEqual(lifecycle.read_registry(self.home).path.read_bytes(), before)
        # Reported rather than silently succeeding, so that running it to ask
        # "what is set" answers the question.
        self.assertIn("already density compact, icons text only", str(plan.to_json()))

    def test_a_preference_change_does_not_read_the_settings_file_at_all(self) -> None:
        self.enable("fornax")
        with self.deny_reads(self.settings):
            self.presentation(glyphs=False)
        self.assertFalse(self.effective().uses_glyphs)

    def test_an_unparseable_settings_file_does_not_block_a_preference(self) -> None:
        self.enable("fornax")
        self.settings.write_bytes(b'{"model": "claude-opus-4",')
        broken = self.settings.read_bytes()

        self.presentation(glyphs=False)

        # Fail-closed exists to stop us writing a configuration we do not
        # understand. It must not stop a user whose configuration is already
        # broken from making the line legible -- that user is the likeliest one to
        # need it, and this operation cannot touch their file.
        self.assertFalse(self.effective().uses_glyphs)
        self.assertEqual(self.settings.read_bytes(), broken)

    def test_a_missing_registry_is_refused_rather_than_created(self) -> None:
        plan = self.plan_presentation(glyphs=False)

        # Creating one here would clear `enable`'s refusal for the case where the
        # compositor owns the slot and the registry -- the only record of the
        # user's original command -- has gone missing.
        self.assertIsNotNone(plan.refusal)
        self.assertFalse(lifecycle.read_registry(self.home).present)
        with self.assertRaises(lifecycle.OwnershipError):
            lifecycle.apply(plan)

    def test_an_unusable_registry_is_not_overwritten_for_a_preference(self) -> None:
        self.enable("fornax")
        registry_path = lifecycle.read_registry(self.home).path
        registry_path.write_bytes(b'{"registry_version": 1, "providers": "not a list"}')
        before = registry_path.read_bytes()

        plan = self.plan_presentation(glyphs=False)

        # MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION, applied to our
        # own file: the providers and the recorded original are in it, and a
        # rendering preference is not worth either of them.
        self.assertIsNotNone(plan.refusal)
        self.assertEqual(registry_path.read_bytes(), before)

    def test_enabling_another_provider_preserves_the_preference(self) -> None:
        self.enable("fornax")
        self.presentation(compact=True, glyphs=False)

        self.enable("circinus")

        # "Do not auto-rewrite user preferences on upgrade" -- which in code means
        # no operation but this one may change the stored mode.
        self.assertEqual(self.effective(), render.PresentationMode.COMPACT_PLAIN)

    def test_re_enabling_the_same_provider_preserves_the_preference(self) -> None:
        self.enable("fornax")
        self.presentation(glyphs=False)
        self.enable("fornax", timeout_ms=400)
        self.assertFalse(self.effective().uses_glyphs)

    def test_disabling_one_provider_preserves_the_preference(self) -> None:
        self.enable("fornax")
        self.enable("circinus")
        self.presentation(glyphs=False)

        self.remove("circinus")

        self.assertFalse(self.effective().uses_glyphs)

    def test_the_last_provider_leaving_takes_the_preference_with_it(self) -> None:
        self.enable("fornax")
        self.presentation(glyphs=False)

        self._uninstall()

        # Deliberate, and the one place the preference does not survive: the
        # preference is our state, and uninstall removes our state. What must
        # survive is the user's, and it does -- their settings file comes back
        # whole. Leaving a preferences file behind after an uninstall would be
        # litter, not a courtesy.
        self.assertFalse(lifecycle.read_registry(self.home).present)
        self.assertEqual(self._read(), self.original)


class RestorationTest(LifecycleCase):
    def test_removing_the_last_provider_restores_only_what_it_took(self) -> None:
        before = self._read()
        self.enable("fornax")
        self.enable("circinus")
        self._uninstall()

        # REMOVE_TOUCHES_ONLY_PRODUCT_OWNED_STATE -- the whole document compared
        # structurally, which is the `A+B → A` case of the invariant with nothing
        # left over: no marker, no compositor command, no leftover keys.
        self.assertEqual(self._read(), before)
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)),
            lifecycle.Ownership.USER_OWNED,
        )

    def test_the_lifecycle_invariant_holds_with_a_user_edit_in_the_middle(self) -> None:
        """`A → A+B → A+B+C → A+C`: only B disappears.

        This is the case a snapshot restore gets wrong. `C` here is deliberately
        placed *inside* the object being restored, because restoring a remembered
        copy of `statusLine` would look correct everywhere except exactly here.
        """
        self.enable("fornax")
        data = self._read()
        data["statusLine"]["padding"] = 4
        data["statusLine"]["aNewKeyTheUserAdded"] = True
        self.write(data)

        self._uninstall()

        after = self._read()
        self.assertEqual(after["statusLine"]["command"], self.original["statusLine"]["command"])
        self.assertEqual(after["statusLine"]["padding"], 4)
        self.assertTrue(after["statusLine"]["aNewKeyTheUserAdded"])
        self.assertNotIn(lifecycle.MARKER_KEY, after["statusLine"])
        self.assertEqual(unowned(after), unowned(self.original))

    def test_the_recorded_original_never_overwrites_a_command_the_user_chose(self) -> None:
        """The strongest form of the no-stale-restore rule.

        The recorded upstream command is the only thing this lifecycle remembers
        about the user's configuration, and it is exactly what a stale-restore bug
        would write back. Here the user has since chosen a different command, so
        writing the remembered one back would be a silent revert of a deliberate
        change.
        """
        self.enable("fornax")
        drifted = self._read()
        drifted["statusLine"]["command"] = "/Users/founder/.claude/a different statusline.sh"
        self.write(drifted)
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)),
            lifecycle.Ownership.DRIFTED,
        )

        plan = self.plan_remove("fornax", operation="uninstall")
        lifecycle.apply(plan)

        after = self._read()
        # STALE_RECEIPT_CANNOT_OVERWRITE_CURRENT_CONFIG
        self.assertEqual(after["statusLine"]["command"], "/Users/founder/.claude/a different statusline.sh")
        self.assertNotIn(lifecycle.MARKER_KEY, after["statusLine"])
        self.assertTrue(any("CONFIG_DRIFT" in change.detail for change in plan.changes))
        self.assertIn(
            "the statusline was changed outside this lifecycle; no command was written back",
            plan.notes,
        )

    def test_removal_never_deletes_the_file_it_shares_with_the_host(self) -> None:
        self.settings.unlink()
        self.enable("fornax")
        self.assertEqual(list(self._read()), [lifecycle.STATUS_LINE_KEY])

        result = self._uninstall()

        # SHARED_CONFIG_IS_NEVER_DELETED_BY_DEFAULT -- this module created both the
        # file and the only entry in it, and still does not remove the file. Claude
        # Code reads it for everything else it does, and a missing file is a
        # different thing to the tool than an empty one.
        self.assertTrue(self.settings.exists())
        self.assertEqual(self._read(), {})
        self.assertNotIn(self.settings, result.plan.state_to_remove)
        # Our own state, which we do exclusively own, is gone.
        self.assertFalse(lifecycle.read_registry(self.home).present)

    def test_a_status_line_we_created_inside_an_existing_file_is_removed_entirely(self) -> None:
        data = rich_settings()
        del data[lifecycle.STATUS_LINE_KEY]
        self.write(data)

        self.enable("fornax")
        plan = self.plan_remove("fornax", operation="uninstall")
        lifecycle.apply(plan)

        self.assertNotIn(lifecycle.STATUS_LINE_KEY, self._read())
        self.assertEqual(self._read(), data)
        self.assertIn(
            "statusLine is removed entirely; it held nothing but this integration", plan.notes
        )

    def test_without_a_recorded_original_the_object_is_left_command_less_not_guessed(self) -> None:
        """The case where there is nothing safe to write and nothing safe to delete.

        A user who adopted an existing configuration has settings this lifecycle
        owns but no record of what preceded it. Inventing a command would be a
        guess; deleting their `padding` and `refreshInterval` with it would be
        destroying state we never owned. So the command goes and the rest stays.
        """
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY] = {
            "type": "command",
            "command": lifecycle.compositor_command(),
            "padding": 2,
            "refreshInterval": 5,
        }
        self.write(data)
        self.enable("fornax", adopt=True)

        plan = self.plan_remove("fornax", operation="uninstall")
        lifecycle.apply(plan)

        after = self._read()
        self.assertEqual(after["statusLine"], {"type": "command", "padding": 2, "refreshInterval": 5})
        self.assertNotIn("command", after["statusLine"])
        self.assertTrue(any("no original command was recorded" in note for note in plan.notes))


class FailSafeTest(LifecycleCase):
    """Cases where the safe answer is to refuse, and refusing must cost nothing."""

    def test_an_unmarked_copy_of_our_own_command_is_not_claimed_on_a_guess(self) -> None:
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY] = {
            "type": "command",
            "command": lifecycle.compositor_command(),
            "padding": 1,
        }
        self.write(data)
        before = self.settings.read_bytes()

        plan = self.plan_enable("fornax")

        # LEGACY_OWNERSHIP_UNKNOWN_FAILS_SAFE -- a configuration that predates
        # ownership markers (or had one stripped) looks exactly like one somebody
        # wrote by hand. It does not license a destructive automatic action; the
        # operator has to say so.
        self.assertIsNotNone(plan.refusal)
        self.assertFalse(plan.mutates)
        with self.assertRaises(lifecycle.OwnershipError):
            lifecycle.apply(plan)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(lifecycle.read_registry(self.home).present)
        self.assertIn("re-run with --adopt if this configuration is yours", plan.remediation)

    def test_ownership_without_the_record_of_the_original_is_a_refusal(self) -> None:
        self.enable("fornax")
        lifecycle.read_registry(self.home).path.unlink()
        before = self.settings.read_bytes()

        plan = self.plan_enable("circinus")

        # The registry holds the only record of the user's original command. Taking
        # a further action while it is missing would strand them.
        self.assertIsNotNone(plan.refusal)
        self.assertIn("registry holds the only record", plan.refusal)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_an_unusable_registry_is_never_silently_rewritten(self) -> None:
        self.enable("fornax")
        self.enable("circinus")
        registry_file = lifecycle.read_registry(self.home).path
        registry_file.write_text('{"registry_version": 99, "providers": []}')
        before = registry_file.read_bytes()

        for plan in (self.plan_enable("libra"), self.plan_remove("fornax")):
            with self.subTest(operation=plan.operation):
                self.assertIsNotNone(plan.refusal)
                with self.assertRaises(lifecycle.OwnershipError):
                    lifecycle.apply(plan)

        # Overwriting it would discard the other product's registration along with
        # the recorded original -- the "let me clean up whatever I can find"
        # behaviour HORO-1000 forbids.
        self.assertEqual(registry_file.read_bytes(), before)

    def test_a_foreign_ownership_marker_is_never_written_over(self) -> None:
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY][lifecycle.MARKER_KEY] = {"owner": "some-other-product"}
        self.write(data)
        before = self.settings.read_bytes()

        plan = self.plan_enable("fornax")

        self.assertIs(plan.ownership, lifecycle.Ownership.UNSUPPORTED_SHAPE)
        self.assertIsNotNone(plan.refusal)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_adopting_is_the_one_explicit_way_past_a_refusal(self) -> None:
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY] = {
            "type": "command",
            "command": lifecycle.compositor_command(),
            "padding": 1,
        }
        self.write(data)

        self.enable("fornax", adopt=True)

        after = self._read()
        self.assertIs(
            lifecycle.classify(lifecycle.read_settings(self.settings)),
            lifecycle.Ownership.HORONOM_OWNED,
        )
        self.assertEqual(after["statusLine"]["padding"], 1)
        # Adoption records ownership. It does not invent an original command, and
        # the registry says so rather than leaving the field absent and ambiguous.
        self.assertNotIn("upstream", self._registry())
        self.assertIsNone(self._registry()["lifecycle"]["created_status_line"])


class MalformedConfigTest(LifecycleCase):
    MALFORMED = (
        b'{"model": "claude-opus-4",',
        b'{"statusLine": {"type": "command", "command": "/x"} "theme": "dark"}',
        b"[]",
        b"not json at all",
        b'{"statusLine": {"type": "command", "command": "/x",}}',
    )

    def test_a_file_that_does_not_parse_stops_every_operation_before_any_write(self) -> None:
        for raw in self.MALFORMED:
            with self.subTest(raw=raw):
                self.settings.write_bytes(raw)
                before = self.settings.read_bytes()

                with self.assertRaises(lifecycle.SettingsParseError):
                    lifecycle.read_settings(self.settings)

                # MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION -- the
                # bytes, not just the parsed value, and the absence of our own
                # state too: a run that refused must not have left a registry
                # behind claiming a provider is installed.
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertFalse(lifecycle.read_registry(self.home).present)

    def test_refusal_is_reported_as_a_refusal_and_not_an_empty_configuration(self) -> None:
        # The specific bug this forbids: a parse error becoming `{}`, after which
        # the next write erases everything the user had.
        self.settings.write_bytes(b'{"model": "claude-opus-4",')
        with self.assertRaises(lifecycle.SettingsParseError):
            lifecycle.read_settings(self.settings)

        report = lifecycle.doctor(self.settings, self.home)
        self.assertFalse(report["settings"]["readable"])
        self.assertTrue(report["drift"]["detected"])
        self.assertEqual(
            report["remediation"],
            [f"repair {self.settings} by hand; no lifecycle operation will write to it until it parses"],
        )



    def test_a_settings_file_we_cannot_read_refuses_and_names_no_paths(self) -> None:
        with self.deny_reads(self.settings):
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                lifecycle.read_settings(self.settings)
            # The errno carries the reason. The OS message carries every parent
            # directory on the way to the file, and those are the user's.
            self.assertIn("errno 13", str(caught.exception))
            self.assertNotIn("Permission denied", str(caught.exception))
            self.assertNotIn("/Users/founder", str(caught.exception))

            # And the diagnostic answers rather than ending in a traceback,
            # which is the whole reason a user runs doctor on a broken host.
            report = lifecycle.doctor(self.settings, self.home)
        self.assertFalse(report["settings"]["readable"])
        self.assertIn("errno 13", report["settings"]["problem"])
        self.assertTrue(report["drift"]["detected"])

    def test_a_registry_we_cannot_read_is_reported_and_blocks_a_write(self) -> None:
        self.enable("fornax")
        registry_path = lifecycle.read_registry(self.home).path
        before = self._read()

        with self.deny_reads(registry_path):
            report = lifecycle.doctor(self.settings, self.home)
            self.assertIn("errno 13", report["drift"]["reason"])
            self.assertTrue(report["drift"]["detected"])

            # MALFORMED_OR_UNSUPPORTED_CONFIG_FAILS_WITH_ZERO_MUTATION -- our own
            # state counts. Registering over a registry we cannot read would
            # discard whatever another product had put in it.
            plan = self.plan_enable("circinus")
            self.assertIsNotNone(plan.refusal)
            self.assertIn("errno 13", plan.refusal)
            self.assertIsNone(plan.settings_after)
            self.assertIsNone(plan.registry_after)

        self.assertEqual(self._read(), before)


class ConcurrentChangeTest(LifecycleCase):
    def test_a_settings_edit_between_plan_and_apply_aborts_the_write(self) -> None:
        plan = self.plan_enable("fornax")

        concurrent = self._read()
        concurrent["theme"] = "changed in another terminal"
        self.write(concurrent)
        before = self.settings.read_bytes()

        with self.assertRaises(lifecycle.ConcurrentModificationError):
            lifecycle.apply(plan)

        # CONCURRENT_CHANGE_DOES_NOT_CLOBBER -- and the concurrent edit is still
        # there, which is the part that matters: detecting the race and then
        # writing anyway would be the same bug with a log line.
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self._read()["theme"], "changed in another terminal")
        self.assertNotIn(lifecycle.MARKER_KEY, self._read()["statusLine"])

    def test_a_registry_edit_between_plan_and_apply_aborts_the_write(self) -> None:
        """The registry is fingerprinted too, and this is why.

        Another product enabling itself in a second terminal writes only the
        registry. A plan that checked the settings file alone would apply happily
        and drop that product's entry -- the settings file it compared is
        untouched, so nothing would look wrong.
        """
        self.enable("fornax")
        plan = self.plan_enable("libra")
        self.enable("circinus")
        before = lifecycle.read_registry(self.home).path.read_bytes()

        with self.assertRaises(lifecycle.ConcurrentModificationError):
            lifecycle.apply(plan)

        self.assertEqual(lifecycle.read_registry(self.home).path.read_bytes(), before)
        self.assertEqual(
            [entry["provider"] for entry in self._registry()["providers"]], ["fornax", "circinus"]
        )


class AtomicWriteTest(LifecycleCase):
    def test_the_destination_never_holds_a_partial_document(self) -> None:
        """Proven by looking at the destination at the moment of publication.

        `os.replace` is the only thing that makes new content visible, so if the
        destination still holds the old bytes when it is called, no reader can ever
        have seen a half-written file. Checking for a leftover temp file afterwards
        would only prove the cleanup ran.
        """
        self.enable("fornax")
        before = self.settings.read_bytes()
        observed: list[bytes] = []
        real_replace = os.replace

        def spy(src, dst, *args, **kwargs):
            if pathlib.Path(dst) == self.settings:
                observed.append(pathlib.Path(dst).read_bytes())
                self.assertEqual(pathlib.Path(src).parent, self.settings.parent)
            return real_replace(src, dst, *args, **kwargs)

        with unittest.mock.patch.object(os, "replace", spy):
            self._uninstall()

        # FAILED_MUTATION_IS_ATOMIC -- one publication, and the old content was
        # still intact right up to it. The temp file is also asserted to be a
        # sibling, because a rename across filesystems is not atomic.
        self.assertEqual(observed, [before])

    def test_a_write_that_fails_leaves_the_original_and_no_debris(self) -> None:
        self.enable("fornax")
        before = self.settings.read_bytes()

        def fail(src, dst, *args, **kwargs):
            raise OSError(28, "No space left on device")

        with unittest.mock.patch.object(os, "replace", fail):
            with self.assertRaises(lifecycle.LifecycleError):
                self._uninstall()

        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.settings.parent.iterdir()), ["settings.json"])

    def test_a_symlink_at_the_temporary_path_is_never_written_through(self) -> None:
        """The temporary name is predictable, and what it carries is credentials.

        `~/.claude` is user-owned, so this is a same-user attack -- but the
        payload is the settings document, `env` and all, and a plain `O_CREAT`
        would have written it wherever the link pointed. Asserting on the decoy's
        contents rather than on an exception, because the safe outcome here is a
        write that simply succeeds somewhere else.
        """
        decoy = self.root / "decoy"
        decoy.write_text("untouched\n")
        self.settings.with_name(f"{self.settings.name}.tmp.{os.getpid()}").symlink_to(decoy)

        lifecycle.atomic_write(self.settings, b'{"ok": true}\n', mode=0o600)

        self.assertEqual(decoy.read_text(), "untouched\n")
        self.assertEqual(self.settings.read_text(), '{"ok": true}\n')

    def test_a_symlink_appearing_in_the_gap_is_refused_rather_than_followed(self) -> None:
        # Unlinking first closes the ordinary case; O_EXCL is what covers the
        # window between that unlink and the open. Simulated by making the unlink
        # a no-op, which is indistinguishable from the link being re-planted the
        # instant it was removed.
        decoy = self.root / "decoy"
        decoy.write_text("untouched\n")
        self.settings.with_name(f"{self.settings.name}.tmp.{os.getpid()}").symlink_to(decoy)
        before = self.settings.read_bytes()

        with unittest.mock.patch.object(pathlib.Path, "unlink", return_value=None):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.atomic_write(self.settings, b'{"statusLine": {}}\n', mode=0o600)

        # FAILED_MUTATION_IS_ATOMIC -- nothing was written, to either file.
        self.assertEqual(decoy.read_text(), "untouched\n")
        self.assertEqual(self.settings.read_bytes(), before)

    def test_a_temporary_file_left_by_a_crash_does_not_wedge_the_next_write(self) -> None:
        # The other half of using O_EXCL: a real file at that name is ours, from a
        # run that died between opening and renaming, and refusing forever because
        # of it would make one crash permanent.
        stale = self.settings.with_name(f"{self.settings.name}.tmp.{os.getpid()}")
        stale.write_text("half a document")
        lifecycle.atomic_write(self.settings, b'{"ok": true}\n', mode=0o600)
        self.assertEqual(self.settings.read_text(), '{"ok": true}\n')
        self.assertFalse(stale.exists())

    def test_a_directory_that_cannot_be_created_is_a_refusal_not_a_traceback(self) -> None:
        # The state directory's parent is a file here, so `mkdir` fails. A raw
        # OSError escaping would be reported to the user as a crash rather than as
        # the refusal it is.
        blocker = self.root / "blocker"
        blocker.write_text("not a directory\n")
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.atomic_write(blocker / "nested" / "registry.json", b"{}", mode=0o600)

    def test_a_settings_read_back_that_disagrees_with_the_plan_is_a_failure(self) -> None:
        # Every other claim in the plan is cleared, so the only thing left that can
        # raise is the settings read-back. An earlier version of this test kept the
        # uninstall plan's `state_to_remove` and passed even with the read-back
        # disabled -- a different check was firing.
        plan = self.plan_enable("fornax")
        lifecycle.apply(plan)
        tampered = dataclasses.replace(
            plan,
            settings_after={"statusLine": {"type": "command", "command": "/never/written"}},
            registry_after=None,
            state_to_remove=(),
        )
        with self.assertRaises(lifecycle.VerificationError):
            lifecycle._verify(tampered)

    def test_a_registry_read_back_that_disagrees_with_the_plan_is_a_failure(self) -> None:
        plan = self.plan_enable("fornax")
        lifecycle.apply(plan)
        tampered = dataclasses.replace(
            plan,
            settings_after=None,
            registry_after=lifecycle.empty_registry(),
            state_to_remove=(),
        )
        with self.assertRaises(lifecycle.VerificationError):
            lifecycle._verify(tampered)

    def test_a_registry_the_compositor_would_reject_is_not_a_success(self) -> None:
        # The interesting question after a write is not "did our bytes land" but
        # "is the statusline still working", and only the compositor's own parser
        # answers that.
        plan = self.plan_enable("fornax")
        lifecycle.apply(plan)
        registry = lifecycle.read_registry(self.home)
        unusable = dict(registry.data) | {"registry_version": 99}
        registry.path.write_bytes(lifecycle.serialize(unusable))
        tampered = dataclasses.replace(
            plan, settings_after=None, registry_after=unusable, state_to_remove=()
        )
        with self.assertRaises(lifecycle.VerificationError):
            lifecycle._verify(tampered)


class HostUsabilityTest(LifecycleCase):
    """The statusline must actually render after every lifecycle step.

    Every other test in this file reads the configuration back. None of them
    prove the host tool can still use it, and the contract is explicit that "a
    preservation test that 'reads back clean' but breaks the tool doesn't count".
    So these run the configured command the way Claude Code does -- through a
    shell, with a Claude-shaped JSON payload on stdin -- and read the line.
    """

    PAYLOAD = json.dumps(
        {
            "hook_event_name": "Status",
            "session_id": "00000000-0000-0000-0000-000000000000",
            "cwd": "/work/repo",
            "model": {"id": "claude-opus-4", "display_name": "Opus"},
            "workspace": {"current_dir": "/work/repo", "project_dir": "/work/repo"},
        }
    ).encode("utf-8")

    def script(self, name: str, body: str) -> pathlib.Path:
        """Write an executable script and pay its first-execution cost up front.

        A freshly written executable costs a large one-time evaluation on macOS,
        easily more than a provider's whole timeout budget. Warming it here means a
        timeout later is a real finding rather than the operating system doing
        first-run bookkeeping.
        """
        path = self.root / name
        path.write_text(body)
        path.chmod(0o755)
        subprocess.run([str(path)], capture_output=True, input=b"{}", timeout=30, check=False)
        return path

    def provider_script(self, name: str, provider: str, label: str) -> pathlib.Path:
        """A provider that emits one valid wire document and nothing else.

        The document is built here with the real contract types, so a provider
        fixture cannot drift into shapes the contract would reject -- which would
        make this test pass for the wrong reason.
        """
        status = contract.ProviderStatus(
            provider=provider,
            provider_version="1.0.0",
            scope=contract.Scope.HOST,
            availability=contract.Availability.AVAILABLE,
            segments=(
                contract.Segment(
                    key="state", state=contract.SegmentState.OK, label=label
                ),
            ),
        )
        payload = json.dumps(status.to_wire())
        return self.script(name, f"#!/bin/sh\ncat >/dev/null\nprintf '%s' {shlex.quote(payload)}\n")

    def render(self) -> str:
        """Run whatever is configured, exactly as the host would."""
        command = self._read()[lifecycle.STATUS_LINE_KEY]["command"]
        completed = subprocess.run(
            command,
            shell=True,
            input=self.PAYLOAD,
            capture_output=True,
            timeout=60,
            env=os.environ | {compositor.STATE_HOME_ENV: str(self.home)},
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        return completed.stdout.decode("utf-8")

    def setUp(self) -> None:
        super().setUp()
        self.upstream = self.script(
            "statusline-dogfood.sh", '#!/bin/sh\ncat >/dev/null\nprintf "MY OWN LINE"\n'
        )
        data = rich_settings()
        data[lifecycle.STATUS_LINE_KEY]["command"] = str(self.upstream)
        self.write(data)

    def test_the_line_renders_at_every_step_of_the_full_lifecycle(self) -> None:
        self.assertEqual(self.render(), "MY OWN LINE")

        fornax = self.provider_script("fornax-provider.sh", "fornax", "VERIFIED")
        self.enable("fornax", argv=(str(fornax),), timeout_ms=2000)
        with_one = self.render()
        # HOST_TOOL_REMAINS_USABLE_AFTER_EACH_LIFECYCLE_STEP, and the ordering rule
        # the compositor owes the user: their own output stays at the front.
        self.assertTrue(with_one.startswith("MY OWN LINE"), with_one)
        self.assertIn("VERIFIED", with_one)

        circinus = self.provider_script("circinus-provider.sh", "circinus", "SHADOW")
        self.enable("circinus", argv=(str(circinus),), timeout_ms=2000)
        with_two = self.render()
        self.assertTrue(with_two.startswith("MY OWN LINE"), with_two)
        self.assertIn("VERIFIED", with_two)
        self.assertIn("SHADOW", with_two)

        self.remove("fornax")
        after_disable = self.render()
        # Disabling one product leaves the other and the user's own line intact.
        self.assertTrue(after_disable.startswith("MY OWN LINE"), after_disable)
        self.assertIn("SHADOW", after_disable)
        self.assertNotIn("VERIFIED", after_disable)

        self._uninstall()
        self.assertEqual(self._read()[lifecycle.STATUS_LINE_KEY]["command"], str(self.upstream))
        self.assertEqual(self.render(), "MY OWN LINE")

    def test_a_failing_provider_cannot_suppress_the_users_own_line(self) -> None:
        broken = self.script("broken-provider.sh", "#!/bin/sh\nprintf 'not json' \nexit 3\n")
        self.enable("broken", argv=(str(broken),), timeout_ms=2000)
        rendered = self.render()
        self.assertTrue(rendered.startswith("MY OWN LINE"), rendered)

    def test_a_command_with_spaces_and_quoting_survives_the_round_trip(self) -> None:
        """The case that breaks any implementation that reassembles the command.

        The path has a space in it and the configured command carries a quoted
        argument. This module never parses the command to store it and never
        rebuilds it to restore it, and this is the fixture that would catch it if
        it started.
        """
        awkward = self.root / "my statusline dir"
        awkward.mkdir()
        script = self.script("my statusline dir/print args.sh", '#!/bin/sh\ncat >/dev/null\nprintf "%s" "$1"\n')
        configured = f'{shlex.quote(str(script))} "a quoted argument"'
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = configured
        self.write(data)
        self.assertEqual(self.render(), "a quoted argument")

        fornax = self.provider_script("fornax-provider.sh", "fornax", "VERIFIED")
        self.enable("fornax", argv=(str(fornax),), timeout_ms=2000)
        self.assertEqual(self._registry()["upstream"]["command"], configured)
        rendered = self.render()
        self.assertTrue(rendered.startswith("a quoted argument"), rendered)

        self._uninstall()
        after = self._read()[lifecycle.STATUS_LINE_KEY]["command"]
        self.assertEqual(after, configured)
        self.assertEqual(self.render(), "a quoted argument")


class CrossModuleTest(LifecycleCase):
    def test_every_registry_this_module_writes_is_one_the_compositor_accepts(self) -> None:
        """The two modules must not be able to drift apart.

        A registry this module can write and the compositor cannot read is a
        statusline that renders nothing, and neither module's own tests would
        notice. `apply` re-validates through the compositor's parser for the same
        reason; this checks the states that parser sees along the way.
        """
        for step in ("enable fornax", "enable circinus", "disable fornax"):
            with self.subTest(step=step):
                if step.startswith("enable"):
                    self.enable(step.split()[1])
                else:
                    self.remove(step.split()[1])
                registry = lifecycle.read_registry(self.home)
                self.assertTrue(registry.usable, registry.problem)
                parsed = compositor.parse_registry(registry.data)
                self.assertEqual(parsed.upstream_command, self.original["statusLine"]["command"])

    def test_the_lifecycle_block_is_ignored_by_the_compositor(self) -> None:
        # The lifecycle's own bookkeeping lives in the same file the compositor
        # reads. It is forward-compatible by construction because that parser drops
        # top-level keys it does not know, and this is the assertion that says so.
        self.enable("fornax")
        registry = lifecycle.read_registry(self.home)
        self.assertIn("lifecycle", registry.data)
        parsed = compositor.parse_registry(registry.data)
        self.assertEqual([entry.provider for entry in parsed.providers], ["fornax"])


class DoctorTest(LifecycleCase):
    def test_the_diagnostic_mutates_nothing_on_any_path(self) -> None:
        for stage in ("nothing installed", "one provider", "drifted"):
            with self.subTest(stage=stage):
                if stage == "one provider":
                    self.enable("fornax")
                if stage == "drifted":
                    data = self._read()
                    data[lifecycle.STATUS_LINE_KEY]["command"] = "/somewhere/else.sh"
                    self.write(data)
                before = self.settings.read_bytes()
                registry_before = (
                    lifecycle.read_registry(self.home).path.read_bytes()
                    if lifecycle.read_registry(self.home).present
                    else None
                )

                lifecycle.doctor(self.settings, self.home)

                self.assertEqual(self.settings.read_bytes(), before)
                registry = lifecycle.read_registry(self.home)
                self.assertEqual(registry.path.read_bytes() if registry.present else None, registry_before)

    def test_running_it_before_anything_is_installed_creates_no_state(self) -> None:
        lifecycle.doctor(self.settings, self.home)
        # A diagnostic that has to create its own state directory to run is one
        # nobody can run to find out whether the product is installed.
        self.assertFalse(self.home.exists())

    def test_it_answers_every_question_a_user_actually_has(self) -> None:
        self.enable("fornax")
        report = lifecycle.doctor(self.settings, self.home)

        self.assertTrue(report["host"]["supported"])
        self.assertEqual(report["host"]["capability"], "statusLine")
        self.assertEqual(report["slot"]["owner"], lifecycle.Ownership.HORONOM_OWNED.value)
        self.assertTrue(report["slot"]["horonom_owned"])
        self.assertTrue(report["slot"]["command"]["is_compositor"])
        self.assertTrue(report["upstream"]["recorded"])
        self.assertEqual(report["upstream"]["name"], "statusline-dogfood.sh")
        self.assertEqual([entry["provider"] for entry in report["providers"]], ["fornax"])
        self.assertFalse(report["drift"]["detected"])
        # HORO-1000's "would this mutate anything" question, answered before the
        # user has to run something to find out.
        self.assertFalse(report["mutation_outlook"]["enabling_another_provider_changes_settings"])
        self.assertTrue(report["mutation_outlook"]["disabling_one_provider_changes_settings"])

    def test_it_reports_drift_and_proposes_a_step_without_taking_one(self) -> None:
        self.enable("fornax")
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = "/Users/founder/.claude/i-changed-my-mind.sh"
        self.write(data)
        before = self.settings.read_bytes()

        report = lifecycle.doctor(self.settings, self.home)

        self.assertTrue(report["drift"]["detected"])
        self.assertEqual(report["slot"]["owner"], lifecycle.Ownership.DRIFTED.value)
        self.assertTrue(report["remediation"])
        self.assertTrue(report["mutation_outlook"]["enabling_another_provider_changes_settings"])
        # Reported, not corrected. Silently retaking the slot is the behaviour a
        # user who deliberately changed their statusline would experience as the
        # tool fighting them.
        self.assertEqual(self.settings.read_bytes(), before)

    def test_it_reports_providers_registered_while_the_slot_is_not_ours(self) -> None:
        self.enable("fornax")
        data = self._read()
        del data[lifecycle.STATUS_LINE_KEY][lifecycle.MARKER_KEY]
        data[lifecycle.STATUS_LINE_KEY]["command"] = str(self.root / "theirs.sh")
        self.write(data)

        report = lifecycle.doctor(self.settings, self.home)

        self.assertEqual(report["slot"]["owner"], lifecycle.Ownership.USER_OWNED.value)
        self.assertEqual(
            report["drift"]["reason"],
            "providers are registered but the compositor does not own the statusline",
        )

    def test_it_never_reproduces_a_value_from_the_settings_file(self) -> None:
        secret = "sk-" + "ant" + "-not-a-real-key-" + "0" * 24
        data = rich_settings()
        data["env"]["ANTHROPIC_API_KEY"] = secret
        data["statusLine"]["command"] = "/Users/founder/.claude/private/secret-statusline.sh"
        self.write(data)

        # Before anything is enabled, so that the command being summarised is the
        # user's own. Diagnosing someone else's statusline is exactly when this is
        # run, and it is the case where the slot holds a path we did not choose.
        untouched = json.dumps(lifecycle.doctor(self.settings, self.home))
        self.assertNotIn("/Users/founder", untouched)
        self.assertIn("secret-statusline.sh", untouched)

        self.enable("fornax", argv=("/Users/founder/private/bin/fornax", "statusline"))

        rendered = json.dumps(lifecycle.doctor(self.settings, self.home))

        self.assertNotIn(secret, rendered)
        self.assertNotIn("sk-", rendered)
        self.assertNotIn("ANTHROPIC_API_KEY", rendered)
        # Full paths from the user's configuration do not appear either -- only the
        # basename, which is what answers "what owns my statusline".
        self.assertNotIn("/Users/founder", rendered)
        self.assertIn("secret-statusline.sh", rendered)
        self.assertIn("fornax", rendered)
        # Unrelated configuration is counted, never listed.
        self.assertEqual(lifecycle.doctor(self.settings, self.home)["settings"]["unrelated_key_count"], 9)

    def test_probing_reports_availability_and_still_changes_no_configuration(self) -> None:
        provider = self.root / "provider.sh"
        status = contract.ProviderStatus(
            provider="fornax",
            provider_version="1.0.0",
            scope=contract.Scope.HOST,
            availability=contract.Availability.UNAVAILABLE,
        )
        provider.write_text(
            f"#!/bin/sh\nprintf '%s' {shlex.quote(json.dumps(status.to_wire()))}\n"
        )
        provider.chmod(0o755)
        subprocess.run([str(provider)], capture_output=True, timeout=30, check=False)

        self.enable("fornax", argv=(str(provider),), timeout_ms=2000)
        before = self.settings.read_bytes()

        report = lifecycle.doctor(self.settings, self.home, probe=True)

        # UNAVAILABLE is reported as itself. A diagnostic that showed a healthy
        # zero here would be the exact confusion the contract forbids.
        self.assertEqual(report["providers"][0]["availability"], "unavailable")
        self.assertEqual(report["providers"][0]["command_name"], "provider.sh")
        self.assertEqual(self.settings.read_bytes(), before)


def segment(key: str, state: str, label: str, **fields) -> contract.Segment:
    """A segment built from wire values, the way a provider's output arrives.

    Strings rather than enum members, because that is what comes off a
    provider's stdout and the decode has to cope with the same input the
    renderer does.
    """
    return contract.Segment(
        key=key,
        state=contract.SegmentState(state),
        label=label,
        confidence=contract.Confidence(fields.pop("confidence"))
        if "confidence" in fields
        else None,
        confidence_of=contract.ConfidenceSubject(fields.pop("confidence_of"))
        if "confidence_of" in fields
        else None,
        **fields,
    )


class ExplainCase(LifecycleCase):
    """A registered provider that really answers, plus a real explain command.

    The provider is a script rather than a patched function: `explain` decodes
    what a provider actually said, so a test that handed it a status object
    directly would skip the part where the answer crosses a process boundary and
    is validated -- which is where a decode of something that is not there would
    otherwise pass.
    """

    def provider_script(self, status: contract.ProviderStatus) -> pathlib.Path:
        path = self.root / f"{status.provider}-provider.sh"
        path.write_text(f"#!/bin/sh\nprintf '%s' {shlex.quote(json.dumps(status.to_wire()))}\n")
        path.chmod(0o755)
        # Run once before it is ever timed. A freshly written executable pays a
        # one-time evaluation cost on macOS that comfortably exceeds a provider's
        # render budget, and a test asserting on a decode must not be measuring
        # that instead.
        subprocess.run([str(path)], capture_output=True, timeout=30, check=False)
        return path

    def detail_script(self, name: str, body: str, *, warm: bool = True) -> pathlib.Path:
        """A stand-in for a product's own explain surface.

        `warm=False` for a script that is not meant to finish -- warming one of
        those would hang the fixture rather than the thing under test. Safe to
        skip there precisely because those tests assert on a timeout, and the
        first-exec cost can only make a slow script slower.
        """
        path = self.root / f"{name}.sh"
        path.write_text(f"#!/bin/sh\n{body}")
        path.chmod(0o755)
        if warm:
            subprocess.run([str(path)], capture_output=True, timeout=30, check=False)
        return path

    def status(
        self,
        provider: str = "fornax",
        *,
        availability: str = "available",
        scope: str = "host",
        segments: tuple[contract.Segment, ...] = (),
    ) -> contract.ProviderStatus:
        return contract.ProviderStatus(
            provider=provider,
            provider_version="1.0.0",
            scope=contract.Scope(scope),
            availability=contract.Availability(availability),
            segments=segments,
        )

    def register(self, status: contract.ProviderStatus, **kwargs) -> None:
        self.enable(
            status.provider,
            argv=(str(self.provider_script(status)),),
            scope=status.scope.value,
            timeout_ms=2000,
            **kwargs,
        )

    def explain(self, **kwargs) -> dict:
        return lifecycle.explain(self.settings, self.home, **kwargs)

    def printed(self, **kwargs) -> str:
        return lifecycle._describe_explain(self.explain(**kwargs))


class ExplainDecodeTest(ExplainCase):
    """What the decode says about a reading, against what the line shows."""

    def test_it_quotes_the_fragment_the_compositor_really_renders(self) -> None:
        # The tie that makes every other assertion here about the user's line
        # rather than about a second renderer. `explain` is asked for its
        # fragment, the compositor is then asked for the whole line, and the
        # fragment has to be in it verbatim.
        self.register(
            self.status(
                segments=(
                    segment("verification", "unknown", "Verification", reason_code="no_daemon"),
                )
            )
        )

        reading = self.explain()["providers"][0]["readings"][0]

        stream = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: str(self.home)}):
            compositor.main(stdin=io.BytesIO(b"{}"), stdout=stream)
        self.assertIn(reading["rendered"], stream.getvalue())

    def test_an_unknown_reading_carries_its_reason_and_its_freshness(self) -> None:
        # Fornax's UNVERIFIED case, which is the one the readability pass was
        # filed about: the state alone left a reader with no idea why.
        self.register(
            self.status(
                segments=(
                    segment(
                        "verification",
                        "unknown",
                        "Verification",
                        reason_code="daemon_not_running",
                        age_seconds=135,
                        explain_key="fornax.verification",
                    ),
                )
            )
        )

        reading = self.explain()["providers"][0]["readings"][0]

        self.assertEqual(reading["reason"], "daemon not running")
        self.assertEqual(reading["freshness"], "2m ago")
        self.assertEqual(reading["explain_key"], "fornax.verification")
        # UNKNOWN != HEALTHY, said in words rather than left to the icon.
        self.assertIn("all clear", reading["state_means"])

    def test_no_reason_is_invented_for_a_provider_that_gave_none(self) -> None:
        # The host does not infer a reason, and specifically does not infer one
        # from a count: "the provider did not say why" is a different claim from
        # any reason the host could construct, and only the first one is true.
        self.register(
            self.status(
                segments=(
                    segment("tasks", "neutral", "Tasks", count=3, total=9, count_label="running"),
                )
            )
        )

        reading = self.explain()["providers"][0]["readings"][0]

        self.assertNotIn("reason", reading)
        self.assertNotIn("freshness", reading)
        self.assertNotIn("why:", self.printed())

    def test_a_would_block_reading_stays_hypothetical_all_the_way_down(self) -> None:
        # Circinus shadow mode. A reader who concludes from this that their agent
        # was stopped has been told something false, so the marker survives into
        # the decode with the denial spelled out rather than just the token.
        self.register(
            self.status(
                provider="circinus",
                segments=(segment("decision", "warn", "Would block", hypothetical=True),),
            )
        )

        reading = self.explain()["providers"][0]["readings"][0]
        printed = self.printed()

        self.assertEqual(reading["hypothetical"], render.HYPOTHETICAL_MEANING)
        self.assertIn(render.HYPOTHETICAL_TEXT, printed)
        self.assertIn("Nothing was blocked", printed)

    def test_a_preflight_confidence_is_decoded_as_a_confidence_not_a_risk(self) -> None:
        # Libra's `pf:high`, which read as "high risk" to the one reader it was
        # built for. The subject is carried into the decode and the meaning says
        # what it is not.
        self.register(
            self.status(
                provider="libra",
                segments=(
                    segment(
                        "preflight",
                        "attention",
                        "Preflight",
                        confidence="high",
                        confidence_of="preflight_estimate",
                    ),
                ),
            )
        )

        confidence = self.explain()["providers"][0]["readings"][0]["confidence"]

        self.assertEqual(confidence["rendered"], "preflight confidence high")
        self.assertIn("Not a risk level", confidence["means"])

    def test_an_unavailable_provider_is_not_decoded_as_a_healthy_zero(self) -> None:
        self.register(self.status(availability="unavailable"))

        provider = self.explain()["providers"][0]

        self.assertEqual(provider["availability"], "unavailable")
        self.assertNotIn("ok", [reading["state"] for reading in provider["readings"]])
        # Named on the line as well, because an availability that only appears
        # when it is bad cannot be told apart from a field nobody filled in.
        self.assertIn("availability: unavailable", self.printed())

    def test_every_token_in_a_decode_has_a_row_in_the_key(self) -> None:
        # The key is generated from the rendering tables and the decode is
        # generated from the same ones, so this is the assertion that catches the
        # two drifting: a reading whose token nothing explains.
        self.register(
            self.status(
                scope="session",
                segments=(
                    segment("a", "critical", "Blocked"),
                    segment(
                        "b",
                        "attention",
                        "Waiting",
                        confidence="medium",
                        confidence_of="policy_decision",
                    ),
                    segment("c", "ok", "Checked", age_seconds=30),
                ),
            )
        )

        report = self.explain()
        tokens = {
            entry["token"] for section in report["legend"] for entry in section["entries"]
        }
        provider = report["providers"][0]

        self.assertIn(provider["scope_token"], tokens)
        for reading in provider["readings"]:
            with self.subTest(key=reading["key"]):
                self.assertIn(reading["state_token"], tokens)
                if "confidence" in reading:
                    # The confidence rows carry an example value, so the row for a
                    # subject is the one that ends in the same subject phrasing.
                    self.assertTrue(
                        any(
                            reading["confidence"]["rendered"].rsplit(" ", 1)[0]
                            == token.rsplit(" ", 1)[0]
                            for token in tokens
                        ),
                        reading["confidence"],
                    )


class ExplainHandoverTest(ExplainCase):
    """Handing the deep explanation to the product that owns it."""

    def test_the_product_speaks_for_itself_and_is_attributed(self) -> None:
        # The host does not paraphrase. A shared surface explaining Fornax's
        # verification states in its own words would be a second copy of Fornax's
        # documentation, kept current by nobody.
        detail = self.detail_script("fornax-explain", "echo 'UNVERIFIED: no daemon has reported'\n")
        self.register(
            self.status(segments=(segment("verification", "unknown", "Verification"),)),
            explain_argv=(str(detail),),
        )

        report = self.explain()["providers"][0]["detail"]

        self.assertTrue(report["available"])
        self.assertEqual(report["text"], "UNVERIFIED: no daemon has reported")
        self.assertEqual(report["command_name"], "fornax-explain.sh")
        self.assertIn("Fornax's own explanation", self.printed())

    def test_a_product_that_registered_none_is_said_to_have_none(self) -> None:
        self.register(self.status(segments=(segment("verification", "ok", "Verified"),)))

        report = self.explain()["providers"][0]["detail"]

        self.assertFalse(report["available"])
        self.assertIsNone(report["text"])
        self.assertIn("registered no explain command", report["problem"])

    def test_a_failing_explain_command_is_reported_and_not_reproduced(self) -> None:
        # Its stderr is not a surface the product designed to be read, and on this
        # workstation a crash message is exactly where a path shows up.
        detail = self.detail_script(
            "angry", "echo 'Traceback: /Users/founder/private/keys.json' >&2\nexit 3\n"
        )
        self.register(
            self.status(segments=(segment("verification", "ok", "Verified"),)),
            explain_argv=(str(detail),),
        )

        printed = self.printed()

        self.assertIn("exited 3", printed)
        self.assertNotIn("Traceback", printed)
        self.assertNotIn("/Users/founder", printed)

    def test_an_explain_command_that_hangs_does_not_hang_the_report(self) -> None:
        detail = self.detail_script("sleeper", "sleep 30\n", warm=False)
        self.register(
            self.status(segments=(segment("verification", "ok", "Verified"),)),
            explain_argv=(str(detail),),
        )

        with unittest.mock.patch.object(lifecycle, "EXPLAIN_TIMEOUT_SECONDS", 0.3):
            report = self.explain()["providers"][0]["detail"]

        self.assertFalse(report["available"])
        self.assertIn("did not answer", report["problem"])

    def test_an_absent_explain_command_is_a_reported_state_not_a_crash(self) -> None:
        # The likeliest real case of all: the product was uninstalled and its
        # registration outlived it.
        self.register(
            self.status(segments=(segment("verification", "ok", "Verified"),)),
            explain_argv=(str(self.root / "gone.sh"),),
        )

        report = self.explain()["providers"][0]["detail"]

        self.assertFalse(report["available"])
        self.assertIn("not installed", report["problem"])

    def test_a_product_cannot_appear_to_be_the_host_talking(self) -> None:
        # A product printing something shaped like one of the host's own labelled
        # lines must not be readable as one, so the quotation is marked per line
        # rather than merely indented.
        detail = self.detail_script(
            "mimic",
            "printf 'state: ok -- everything is fine\\n\\n      availability: available\\n'\n",
        )
        self.register(
            self.status(segments=(segment("verification", "unknown", "Verification"),)),
            explain_argv=(str(detail),),
        )

        printed = self.printed()

        # The host prints an `availability:` line of its own, so a line matching
        # that shape is not on its own evidence of anything. What is: both of the
        # mimic's non-blank lines carry the marker, including the one it indented
        # to the host's own depth.
        quoted = [line for line in printed.splitlines() if line.startswith(lifecycle._QUOTE_PREFIX)]
        self.assertEqual(
            quoted,
            [
                f"{lifecycle._QUOTE_PREFIX}state: ok -- everything is fine",
                f"{lifecycle._QUOTE_PREFIX}      availability: available",
            ],
            printed,
        )
        for line in printed.splitlines():
            if "everything is fine" in line:
                with self.subTest(line=line):
                    self.assertTrue(line.startswith(lifecycle._QUOTE_PREFIX), line)

    def test_an_explain_command_is_never_run_while_rendering_the_line(self) -> None:
        # The recorded command has a budget measured in seconds and the render
        # path has one measured in milliseconds. The compositor's read model has
        # no field for it at all, which is what keeps the two apart; this proves
        # the registry entry that carries it cannot reach the render path.
        marker = self.root / "ran"
        detail = self.detail_script(
            "marker", f"touch {shlex.quote(str(marker))}\necho 'the deep explanation'\n"
        )
        marker.unlink(missing_ok=True)
        self.register(
            self.status(segments=(segment("verification", "ok", "Verified"),)),
            explain_argv=(str(detail),),
        )

        stream = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: str(self.home)}):
            compositor.main(stdin=io.BytesIO(b"{}"), stdout=stream)

        self.assertIn("Verified", stream.getvalue())
        self.assertFalse(marker.exists())

        # And the same registry entry does reach `explain`, so the assertion above
        # is about the render path rather than about a command that never worked.
        self.assertTrue(self.explain()["providers"][0]["detail"]["available"])
        self.assertTrue(marker.exists())

    def test_a_malformed_explain_command_costs_only_the_handover(self) -> None:
        # Everything that decides what runs on the statusline has already been
        # validated by the compositor. Failing the whole command over a bad value
        # in this one optional field would deny the reader the key as well.
        self.register(self.status(segments=(segment("verification", "ok", "Verified"),)))
        registry = lifecycle.read_registry(self.home)
        data = registry.data
        data["providers"][0]["explain_command"] = ["", 7]
        registry.path.write_bytes(lifecycle.serialize(data, indent=2))

        report = self.explain()

        self.assertIn("registered no explain command", report["providers"][0]["detail"]["problem"])
        self.assertTrue(report["legend"])
        self.assertTrue(report["providers"][0]["readings"])


class ExplainReadOnlyTest(ExplainCase):
    """The command a reader runs when the line confuses them, changing nothing."""

    def test_it_mutates_nothing_on_any_path(self) -> None:
        for stage in ("nothing installed", "one provider", "drifted", "unusable registry"):
            with self.subTest(stage=stage):
                if stage == "one provider":
                    self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
                if stage == "drifted":
                    data = self._read()
                    data[lifecycle.STATUS_LINE_KEY]["command"] = "/somewhere/else.sh"
                    self.write(data)
                if stage == "unusable registry":
                    lifecycle.read_registry(self.home).path.write_bytes(b"{ truncated")
                before = self.settings.read_bytes()
                registry = lifecycle.read_registry(self.home)
                registry_before = registry.path.read_bytes() if registry.present else None

                self.explain()

                self.assertEqual(self.settings.read_bytes(), before)
                after = lifecycle.read_registry(self.home)
                self.assertEqual(after.path.read_bytes() if after.present else None, registry_before)

    def test_running_it_before_anything_is_installed_creates_no_state(self) -> None:
        report = self.explain()

        # Same reason `doctor` may not: a surface you run to find out whether the
        # product is installed must not install part of it in the process.
        self.assertFalse(self.home.exists())
        self.assertTrue(report["legend"])
        notes = " ".join(report["notes"])
        self.assertIn("not Horonom-owned", notes)
        self.assertIn("no providers are registered", notes)

    def test_the_legend_alone_asks_no_provider_anything(self) -> None:
        # `--legend` is for a reader who wants the vocabulary, not a reading. It
        # should cost nothing, and a provider is a subprocess.
        marker = self.root / "probed"
        script = self.root / "provider.sh"
        script.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 1\n")
        script.chmod(0o755)
        self.enable("fornax", argv=(str(script),), timeout_ms=2000)

        report = self.explain(legend_only=True)

        self.assertFalse(marker.exists())
        self.assertTrue(report["legend"])
        self.assertEqual(report["providers"], [])

    def test_an_unusable_registry_still_yields_the_key(self) -> None:
        # The key is built from the host's own rendering tables, so it is correct
        # before anything is measured -- and a reader whose registry is broken is
        # exactly a reader looking at a line they cannot read.
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        lifecycle.read_registry(self.home).path.write_bytes(b"{ truncated")

        report = self.explain()

        self.assertTrue(report["legend"])
        self.assertEqual(report["providers"], [])
        self.assertIn("the key itself is still correct", " ".join(report["notes"]))

    def test_an_unreadable_settings_file_does_not_stop_the_decode(self) -> None:
        # What is on the line cannot be confirmed without the settings file, but
        # what the providers say can still be decoded, and saying so is more use
        # than refusing to answer at all.
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))

        with self.deny_reads(self.settings):
            report = self.explain()

        self.assertIn("could not be read", " ".join(report["notes"]))
        self.assertEqual([p["provider"] for p in report["providers"]], ["fornax"])
        self.assertTrue(report["providers"][0]["readings"])

    def test_it_says_when_nothing_of_ours_is_on_the_line(self) -> None:
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = "/Users/founder/.claude/i-changed-my-mind.sh"
        self.write(data)

        notes = " ".join(self.explain()["notes"])

        self.assertIn("not Horonom-owned", notes)
        self.assertIn("statusline doctor", notes)

    def test_it_prints_no_value_out_of_the_settings_file(self) -> None:
        # The file it reads holds an API-key-shaped env var, an internal URL and
        # the user's home path. None of them is this command's business, and the
        # reason it can promise that is that it reads the file to classify the
        # slot and never to print from it.
        data = self._read()
        data["env"]["ANTHROPIC_AUTH_TOKEN"] = "sk-ant-notarealkey-0123456789"
        data["env"]["FOUNDER_BIN"] = "/Users/founder/private/bin"
        self.write(data)
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))

        printed = self.printed()

        for secret in (
            "sk-ant-notarealkey-0123456789",
            "ANTHROPIC_AUTH_TOKEN",
            "api.example.invalid",
            "/Users/founder/private/bin",
            "managed-settings.json",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, printed)

        # The user's own statusline path is not in the settings file any more --
        # it moved into the registry when the slot was taken over -- so assert
        # against the place it actually lives, or the line above would be proving
        # the absence of something that was never there to leak.
        upstream = lifecycle.read_registry(self.home).data["upstream"]["command"]
        self.assertIn("/Users/founder", upstream)
        self.assertNotIn(upstream, printed)


class ExplainPresentationTest(ExplainCase):
    """The key is drawn in the mode the line is drawn in, and says which."""

    def test_the_key_is_drawn_in_the_mode_actually_in_force(self) -> None:
        # A key printed in glyphs to a reader whose terminal is why they turned
        # glyphs off is worse than no key: every row is a question rather than an
        # answer.
        self.register(self.status(segments=(segment("v", "unknown", "Verification"),)))
        lifecycle.apply(lifecycle.plan_presentation(lifecycle.read_registry(self.home), glyphs=False))

        report = self.explain()

        self.assertEqual(report["presentation"]["source"], "your saved preference")
        self.assertFalse(report["presentation"]["mode"].endswith("glyph"))
        printed = self.printed()
        self.assertTrue(printed.isascii(), printed)

    def test_the_default_is_named_as_the_default(self) -> None:
        # So a reader can tell "this is what you asked for" from "this is what you
        # get when you have asked for nothing", which is the difference between a
        # preference that did not apply and one that was never set.
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))

        self.assertEqual(self.explain()["presentation"]["source"], "the default")

    def test_a_preference_this_version_cannot_read_says_so(self) -> None:
        # A mode written by a newer version, or by hand. The renderer resolves it
        # to something drawable either way, and reporting that silently would
        # leave a reader comparing a line against a key for a mode they asked for
        # and are not getting.
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        registry = lifecycle.read_registry(self.home)
        data = registry.data
        data[lifecycle.PRESENTATION_KEY] = {"mode": "holographic"}
        registry.path.write_bytes(lifecycle.serialize(data, indent=2))

        presentation = self.explain()["presentation"]

        self.assertIn("not a mode this version knows", presentation["source"])
        self.assertEqual(presentation["mode"], render.PresentationMode.BALANCED.value)


class ExplainCommandLineTest(ExplainCase):
    """Narrowing the decode, and reaching it through the CLI."""

    def test_naming_a_provider_decodes_only_that_one(self) -> None:
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        self.register(self.status("libra", segments=(segment("p", "attention", "Preflight"),)))

        report = self.explain(provider="libra")

        self.assertEqual([p["provider"] for p in report["providers"]], ["libra"])
        self.assertEqual(report["notes"], [])

    def test_an_unregistered_name_is_answered_rather_than_ignored(self) -> None:
        # Silently printing the key and no readings would read as "that provider
        # has nothing to say", which is a different and untrue claim.
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))

        report = self.explain(provider="eltanin")

        self.assertEqual(report["providers"], [])
        self.assertIn("no provider named 'eltanin'", " ".join(report["notes"]))

    def test_the_subcommand_writes_nothing_and_succeeds(self) -> None:
        self.register(self.status(segments=(segment("v", "unknown", "Verification"),)))
        before = self.settings.read_bytes()
        registry_before = lifecycle.read_registry(self.home).path.read_bytes()
        stream = io.StringIO()

        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: str(self.home)}):
            code = lifecycle.main(["explain", "--settings", str(self.settings)], stdout=stream)

        # Zero even though the report carries notes. Every state this command can
        # report is one it was asked to describe, including "nothing of ours is on
        # your line" -- that is an answer, not a failure to give one.
        self.assertEqual(code, lifecycle.EXIT_OK)
        self.assertIn("how to read the line", stream.getvalue())
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(lifecycle.read_registry(self.home).path.read_bytes(), registry_before)

    def test_a_note_is_not_a_refusal(self) -> None:
        # `doctor` exits non-zero on drift, because it is answering "is this
        # installed correctly" and the answer is no. This command answers "what is
        # the line saying", and "nothing of ours, someone else owns the slot" is a
        # complete answer to that.
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = "/somewhere/else.sh"
        self.write(data)
        stream = io.StringIO()

        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: str(self.home)}):
            code = lifecycle.main(["explain", "--settings", str(self.settings)], stdout=stream)

        self.assertEqual(code, lifecycle.EXIT_OK)
        self.assertIn("note: ", stream.getvalue())
        self.assertNotEqual(
            lifecycle.main(["doctor", "--settings", str(self.settings)], stdout=io.StringIO()),
            lifecycle.EXIT_OK,
        )

    def test_the_json_form_carries_the_whole_report(self) -> None:
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        stream = io.StringIO()

        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: str(self.home)}):
            lifecycle.main(
                ["explain", "--json", "--settings", str(self.settings)], stdout=stream
            )

        report = json.loads(stream.getvalue())
        self.assertEqual(
            sorted(report), ["legend", "notes", "presentation", "providers", "registry_path"]
        )
        self.assertEqual(report["providers"][0]["provider"], "fornax")

    def test_the_legend_flag_reaches_the_report(self) -> None:
        self.register(self.status(segments=(segment("v", "ok", "Verified"),)))
        stream = io.StringIO()

        with unittest.mock.patch.dict(os.environ, {compositor.STATE_HOME_ENV: str(self.home)}):
            lifecycle.main(
                ["explain", "--legend", "--json", "--settings", str(self.settings)], stdout=stream
            )

        self.assertEqual(json.loads(stream.getvalue())["providers"], [])

    def test_a_provider_name_is_positional_here(self) -> None:
        # Unlike the mutating subcommands, where naming the provider explicitly is
        # worth the typing. Here it only narrows a report, and `explain fornax` is
        # what a reader reaches for.
        options = lifecycle.build_parser().parse_args(["explain", "fornax"])

        self.assertEqual(options.provider, "fornax")
        self.assertFalse(options.legend_only)


class CommandLineTest(LifecycleCase):
    """The CLI exists so that nobody has to hand-edit JSON, so it is tested as the surface."""

    def cli(self, *arguments: str) -> tuple[int, str]:
        stream = io.StringIO()
        with unittest.mock.patch.dict(
            os.environ, {compositor.STATE_HOME_ENV: str(self.home)}
        ):
            code = lifecycle.main(
                [*arguments, "--settings", str(self.settings)], stdout=stream
            )
        return code, stream.getvalue()

    def test_a_dry_run_prints_the_plan_and_writes_nothing(self) -> None:
        before = self.settings.read_bytes()

        code, output = self.cli(
            "enable", "--provider", "fornax", "--scope", "host", "--command", "/bin/echo", "--dry-run"
        )

        self.assertEqual(code, lifecycle.EXIT_OK)
        self.assertIn("host_user_state_preserved", output)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(lifecycle.read_registry(self.home).present)

    def test_the_dry_run_and_the_real_run_describe_the_same_plan(self) -> None:
        _, preview = self.cli(
            "enable", "--provider", "fornax", "--scope", "host", "--command", "/bin/echo",
            "--dry-run", "--json",
        )
        _, applied = self.cli(
            "enable", "--provider", "fornax", "--scope", "host", "--command", "/bin/echo", "--json"
        )
        # The preview is the same object the mutation acts on. A preview generated
        # by separate code is a preview of nothing.
        self.assertEqual(json.loads(preview)["changes"], json.loads(applied)["changes"])

    def test_a_malformed_settings_file_is_refused_before_a_plan_exists(self) -> None:
        self.settings.write_bytes(b'{"model": "claude-opus-4",')
        before = self.settings.read_bytes()

        code, output = self.cli(
            "enable", "--provider", "fornax", "--scope", "host", "--command", "/bin/echo"
        )

        self.assertEqual(code, lifecycle.EXIT_REFUSED)
        self.assertIn("refused", output)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(lifecycle.read_registry(self.home).present)

    def test_an_unusable_provider_id_is_refused_and_named(self) -> None:
        code, output = self.cli(
            "enable", "--provider", "Not A Provider Id", "--scope", "host", "--command", "/bin/echo"
        )
        self.assertEqual(code, lifecycle.EXIT_REFUSED)
        self.assertIn("refused", output)
        self.assertFalse(lifecycle.read_registry(self.home).present)

    def test_the_whole_lifecycle_is_reachable_from_the_command_line(self) -> None:
        for arguments in (
            ("enable", "--provider", "fornax", "--scope", "host", "--command", "/bin/echo"),
            ("enable", "--provider", "circinus", "--scope", "session", "--command", "/bin/echo"),
            ("list",),
            ("doctor",),
            ("disable", "--provider", "circinus"),
            ("uninstall",),
        ):
            with self.subTest(command=arguments[0]):
                code, output = self.cli(*arguments)
                self.assertEqual(code, lifecycle.EXIT_OK, output)
                self.assertTrue(output.strip())

        self.assertEqual(self._read(), self.original)
        self.assertFalse(lifecycle.read_registry(self.home).present)

    def test_doctor_exits_non_zero_when_it_has_found_drift(self) -> None:
        # So that a script can ask "is this healthy" without parsing prose.
        self.cli("enable", "--provider", "fornax", "--scope", "host", "--command", "/bin/echo")
        self.assertEqual(self.cli("doctor")[0], lifecycle.EXIT_OK)

        data = self._read()
        data[lifecycle.STATUS_LINE_KEY]["command"] = "/somewhere/else.sh"
        self.write(data)
        code, output = self.cli("doctor")
        self.assertEqual(code, lifecycle.EXIT_REFUSED)
        self.assertIn("drift: yes", output)

    def test_listing_what_is_on_survives_an_unparseable_settings_file(self) -> None:
        # The state a user runs `list` to understand must not be the state that
        # raises. This asserted a traceback before the guard in `main` was added.
        self.settings.write_bytes(b"{ not json")

        code, output = self.cli("list")

        self.assertEqual(code, lifecycle.EXIT_OK)
        self.assertIn(lifecycle.Ownership.UNSUPPORTED_SHAPE.value, output)


if __name__ == "__main__":
    unittest.main()
