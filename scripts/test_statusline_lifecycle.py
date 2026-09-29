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


if __name__ == "__main__":
    unittest.main()
