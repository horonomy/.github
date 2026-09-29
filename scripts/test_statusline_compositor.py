"""Tests for the statusline compositor.

Two groups here are load-bearing rather than merely thorough, and deleting a
test from either weakens a guarantee the product makes to the user:

`TestUpstreamIsNeverOurs` proves the user's own statusline survives everything
we can do to ourselves — its command string reaches the shell unmodified, it
receives the same stdin bytes we were given, its output is a verbatim prefix of
ours, and no failure of ours suppresses it.

`TestNothingLeaksAndNothingLeaksOut` proves the two containment promises: a
timed-out provider leaves no process behind (this command re-runs every few
seconds for the life of a session, so a per-refresh leak accumulates), and
neither a provider's output nor our own error paths put a secret, a path or an
exception message onto the rendered line.

The subprocess tests use real children because every guarantee being checked is
a guarantee about real process behaviour; a mocked `Popen` would prove only that
the mock matches my belief about it. Fixtures are warmed once before use: on
macOS a freshly written executable pays a large one-time evaluation cost that
would otherwise show up as a spurious timeout.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

import statusline_compositor as compositor
import statusline_contract as contract
import statusline_render as render

# Generous, because these must pass even on a loaded machine: where a test
# asserts success, a timeout is a false failure.
GENEROUS_MS = 2000
# Short, because these are the cases where a timeout is the expected result and
# the child is asleep for far longer than any scheduling jitter.
IMPATIENT_MS = 200
# Long enough that a leaked child is still running when we look for it, short
# enough that one surviving a test run cleans itself up.
LEAK_SLEEP = "27"
# An upper bound on the first-execution cost of a newly written file, not an
# expected duration: warming normally finishes in a fraction of this.
WARM_LIMIT_SECONDS = 10.0


def wire(**overrides) -> dict:
    """A minimal valid provider answer, as a provider would print it."""
    payload = {
        "contract_version": 1,
        "provider": "fornax",
        "provider_version": "0.4.1",
        "scope": "project",
        "availability": "available",
        "segments": [{"key": "latest_verdict", "state": "ok", "label": "Verified"}],
    }
    payload.update(overrides)
    return payload


def registry_document(**overrides) -> dict:
    """A minimal valid registry document."""
    payload = {"registry_version": 1, "providers": []}
    payload.update(overrides)
    return payload


def provider_document(provider: str, command: list[str], **overrides) -> dict:
    payload = {"provider": provider, "command": command, "scope": "project"}
    payload.update(overrides)
    return payload


if __name__ == "__main__":
    unittest.main()
