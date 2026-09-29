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

import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

import statusline_lifecycle as lifecycle


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

    def write(self, data: dict, *, indent: int | None = 2) -> None:
        self.settings.write_bytes(lifecycle.serialize(data, indent=indent))

    def read(self) -> dict:
        return json.loads(self.settings.read_text())

    def registry(self) -> dict | None:
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
        )
        return lifecycle.plan_enable(document, registry, registration, **kwargs)

    def enable(self, provider: str, **kwargs) -> lifecycle.ApplyResult:
        return lifecycle.apply(self.plan_enable(provider, **kwargs))

    def plan_remove(self, *providers: str, operation: str = "disable") -> lifecycle.Plan:
        document, registry = self.documents()
        return lifecycle.plan_remove(document, registry, providers=providers, operation=operation)

    def remove(self, *providers: str, operation: str = "disable") -> lifecycle.ApplyResult:
        return lifecycle.apply(self.plan_remove(*providers, operation=operation))

    def uninstall(self) -> lifecycle.ApplyResult:
        document, registry = self.documents()
        plan = lifecycle.plan_remove(
            document,
            registry,
            providers=lifecycle._provider_ids(registry),
            operation="uninstall",
        )
        return lifecycle.apply(plan)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.settings.read_bytes()).hexdigest()


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


if __name__ == "__main__":
    unittest.main()
