"""The single statusline command Horonom owns, and everything it multiplexes.

Claude Code gives a scope exactly one `statusLine.command`. Several Horonom
products want to say something there, and the user usually already has their own
script in that slot. This module is what makes those compatible: it becomes the
one configured command, runs the user's original command as an *upstream*
provider, runs the registered Horonom providers beside it, and prints the
original output followed by a bounded Horonom block.

The invariants, in the order they matter:

1. **The user's line is not ours.** Their configured command string is passed to
   the shell unmodified, receives the same stdin bytes we were given, and its
   stdout is reproduced verbatim as the prefix of our output. It is never
   edited, parsed, rewritten, reordered, shortened or annotated, and no failure
   of ours may suppress it. "No upstream" is a valid state, not an error.
2. **Nothing expensive happens here.** This runs on every statusline refresh.
   No LLM call, no network request, no install, no build, no daemon start, no
   filesystem walk. Providers are subprocesses with a per-provider timeout under
   a bounded overall deadline, run concurrently, so the wall clock is roughly
   the slowest single provider rather than the sum of the timeouts.
3. **A failure is reported, not hidden.** A provider that times out, crashes,
   emits unparseable output or violates the contract becomes an explicit
   not-available status. It never becomes silence, and it never becomes a
   healthy zero — `statusline_contract` refuses that shape outright.
4. **We do not read the payload.** The JSON the host writes to our stdin is
   consumed as opaque bytes and forwarded. Not parsing it means there is no
   schema of the host's to track, and nothing from it can leak into a rendered
   label by accident.

Config *mutation* is deliberately not here: this module only reads the registry
that the install lifecycle writes (HORO-1566). It creates its own cache
directory and nothing else.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import statusline_contract as contract
import statusline_render as render

# Horonom-owned state. Everything this module writes lives under here, and it
# writes nothing anywhere else — in particular nothing under the host's own
# configuration directory, which it has no ownership of.
STATE_HOME_ENV = "HORONOM_STATUSLINE_HOME"
DEFAULT_STATE_HOME = "~/.horonom/statusline"
REGISTRY_FILENAME = "registry.json"
CACHE_DIRNAME = "cache"

# Set on every child process. A registry whose upstream points back at this
# command would otherwise fork bomb the host one refresh at a time.
DEPTH_ENV = "HORONOM_STATUSLINE_DEPTH"
MAX_DEPTH = 1

REGISTRY_VERSION = 1
SUPPORTED_REGISTRY_VERSIONS = frozenset({REGISTRY_VERSION})


if __name__ == "__main__":
    sys.exit(main())
