"""Path safety for the fixture capture driver (HORO-1635).

`statusline_capture.py` clears two *predictable* paths, and that part cannot be
designed away: `AF_UNIX` is capped at 104 bytes on macOS and `$TMPDIR` there is
long enough on its own to blow the cap, so the staged daemons' socket root has to
be short and named rather than a `mkdtemp`. Two separate things follow from that,
and this module covers both.

**Where the roots live.** A short path does not have to be a world-writable one.
Both roots now sit under `$HOME`, so the pre-created hostile directory is mostly
designed away rather than only defended against — `StagingRootLocationTest` is
what keeps them there, and keeps the socket root short enough to be usable there.

**That the guards hold anyway.** A predictable path can still exist already, from
a previous run or from a hand that is not ours, so the guards still have to
refuse. The standard each case is held to is **fail closed with zero mutation**:
a root the script does not recognise as its own must produce a refusal *and*
leave what was there untouched. A refusal that has already deleted something is
not a refusal, which is why every negative case here plants a canary and asserts
it survived rather than only asserting that an exception was raised.

Nothing here runs the capture itself — that needs all three products installed.
These exercise the guards and the path choices in front of it.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

import statusline_capture as capture


def parser_default(flag: str) -> pathlib.Path:
    """The parser's own default for `flag`, rather than a copy of it here.

    `--work-dir`'s default is a security-relevant choice, so more than one test
    asserts on it. Reading it off the parser means none of them can drift away
    from the value the script uses, which restating the path would allow.
    """
    for action in capture.build_parser()._actions:
        if flag in action.option_strings:
            return action.default
    raise AssertionError(f"{flag} is no longer a command-line option")


class PrivateStagingRootTest(unittest.TestCase):
    """`reset_private_dir` — the guard in front of both staging roots."""

    def setUp(self) -> None:
        # The system temp dir, not a predictable path: these tests must not
        # themselves depend on being first to a well-known name.
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="horo-capture-guard-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        for path in self.base.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        shutil.rmtree(self.base, ignore_errors=True)

    def test_a_fresh_root_is_created_private_to_this_user(self) -> None:
        root = capture.reset_private_dir(self.base / "fresh")
        self.assertTrue(root.is_dir())
        self.assertEqual(
            root.stat().st_mode & 0o777,
            0o700,
            "a staged daemon writes product state here; another account must not be "
            "able to enter it",
        )

    def test_the_mode_survives_a_permissive_umask(self) -> None:
        # `mkdir(mode=...)` is masked by the umask, so the explicit `chmod` is
        # what actually carries the guarantee. Without it a 0o022 umask leaves
        # the root group- and world-readable and nothing here would notice.
        import os

        previous = os.umask(0o000)
        self.addCleanup(os.umask, previous)
        root = capture.reset_private_dir(self.base / "umask")
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_an_existing_private_root_of_ours_is_emptied(self) -> None:
        root = self.base / "reused"
        root.mkdir(mode=0o700)
        (root / "left-over").write_text("from a previous run")
        capture.reset_private_dir(root)
        self.assertEqual(list(root.iterdir()), [], "a staging root is throwaway by contract")

    def test_a_root_other_accounts_can_enter_is_refused_and_left_alone(self) -> None:
        root = self.base / "hostile"
        root.mkdir()
        root.chmod(0o777)
        canary = root / "canary"
        canary.write_text("planted")
        with self.assertRaises(SystemExit) as caught:
            capture.reset_private_dir(root)
        self.assertIn("accessible to other accounts", str(caught.exception))
        self.assertTrue(canary.exists(), "refusing must not be the same thing as clearing")

    def test_a_symlinked_root_is_refused_and_its_target_is_left_alone(self) -> None:
        # The shape this exists for: the root already exists as a link to
        # something that matters, and the script empties it on the owner's
        # behalf. Under `$HOME` the likely author of that link is a previous
        # tool of the owner's own rather than another account, which changes who
        # did it and not what it costs.
        target = self.base / "target"
        target.mkdir(mode=0o700)
        precious = target / "precious"
        precious.write_text("not yours to delete")
        link = self.base / "link"
        link.symlink_to(target)
        with self.assertRaises(SystemExit) as caught:
            capture.reset_private_dir(link)
        self.assertIn("symlink", str(caught.exception))
        self.assertTrue(precious.exists())
        self.assertTrue(link.is_symlink(), "the link itself is the operator's to remove")

    def test_a_root_that_is_not_a_directory_is_refused(self) -> None:
        plain = self.base / "a-file"
        plain.write_text("x")
        with self.assertRaises(SystemExit) as caught:
            capture.reset_private_dir(plain)
        self.assertIn("not a directory", str(caught.exception))
        self.assertEqual(plain.read_text(), "x")

    def test_a_relative_root_is_refused(self) -> None:
        # A relative staging root would resolve against whatever directory the
        # capture happened to be launched from, which for a script that clears
        # the path is the whole problem.
        with self.assertRaises(SystemExit) as caught:
            capture.reset_private_dir(pathlib.Path("relative/root"))
        self.assertIn("absolute", str(caught.exception))


class StagingRootLocationTest(unittest.TestCase):
    """Where the predictable roots live, which is the half guards cannot fix.

    A validated root under `/tmp` is still a root another account can race us to
    and still a root whose contents another account can read. The guards make the
    race lose; keeping the path out of a world-writable directory means there is
    no race to run.
    """

    def test_neither_staging_root_sits_in_a_world_writable_directory(self) -> None:
        home = pathlib.Path.home()
        default_work_dir = parser_default("--work-dir")
        for name, root in (("SOCKET_ROOT", capture.SOCKET_ROOT), ("--work-dir", default_work_dir)):
            with self.subTest(root=name):
                self.assertTrue(
                    root.is_relative_to(home),
                    f"{name} is {root}; a staging root holding product state belongs "
                    "under $HOME, not somewhere every account on the machine can write",
                )

    def test_the_socket_root_leaves_room_for_the_sockets_bound_under_it(self) -> None:
        # The constraint that put this root outside the work directory in the
        # first place. Asserted so a future move somewhere more deeply nested
        # fails here rather than inside a daemon start, where it surfaces as a
        # bare OSError with no mention of path length.
        self.assertGreaterEqual(
            capture.socket_budget(capture.SOCKET_ROOT),
            45,
            f"{capture.SOCKET_ROOT} leaves too little of the {capture.AF_UNIX_MAX}-byte "
            "AF_UNIX cap for a per-case directory plus a socket name",
        )

    def test_the_budget_is_measured_in_bytes_not_characters(self) -> None:
        # A home directory with a non-ASCII name costs more bytes than it has
        # characters, and `sun_path` is a byte buffer.
        wide = pathlib.Path("/home/ééé")
        self.assertEqual(
            capture.socket_budget(wide),
            capture.AF_UNIX_MAX - len(str(wide).encode()) - 1,
        )
        self.assertLess(capture.socket_budget(wide), capture.AF_UNIX_MAX - len(str(wide)) - 1)


class StagingParentConfinementTest(unittest.TestCase):
    """`confine_to_staging_parent` — the guard that asks whether we may *delete* here.

    `reset_private_dir`'s checks are about writing: absolute, ours, private, not a
    symlink. Every one of them passes on `~/.ssh`, and the step after them is
    `rmtree`. So the location is a separate question with a separate guard, and
    these are the cases that distinguish the two.
    """

    def test_the_default_work_dir_is_accepted(self) -> None:
        # The rule has to admit the value the script actually ships with, or it
        # is a rule that only fires on the operator.
        default = parser_default("--work-dir")
        self.assertEqual(
            capture.confine_to_staging_parent(default, what="--work-dir"),
            default.resolve(),
        )

    def test_the_socket_root_is_accepted(self) -> None:
        self.assertEqual(
            capture.confine_to_staging_parent(capture.SOCKET_ROOT, what="SOCKET_ROOT"),
            capture.SOCKET_ROOT.resolve(),
        )

    def test_a_private_directory_of_ours_elsewhere_in_home_is_refused(self) -> None:
        # The case the write-side guard cannot catch. `~/.ssh` is absolute, ours,
        # 0700 and a real directory, so `reset_private_dir` would clear it.
        elsewhere = pathlib.Path.home() / ".ssh"
        with self.assertRaises(SystemExit) as caught:
            capture.confine_to_staging_parent(elsewhere, what="--work-dir")
        self.assertIn("directory of its own", str(caught.exception))
        self.assertIn("--work-dir", str(caught.exception))

    def test_the_staging_parent_itself_is_refused(self) -> None:
        # `--work-dir ~/.cache` would delete every other tool's cache.
        with self.assertRaises(SystemExit):
            capture.confine_to_staging_parent(capture.STAGING_PARENT, what="--work-dir")

    def test_a_dot_dot_escape_out_of_the_staging_parent_is_refused(self) -> None:
        escape = capture.STAGING_PARENT / ".." / "Documents"
        with self.assertRaises(SystemExit):
            capture.confine_to_staging_parent(escape, what="--work-dir")

    def test_a_deeper_directory_inside_the_staging_parent_is_accepted(self) -> None:
        # Nesting is not the thing being prevented; leaving is.
        nested = capture.STAGING_PARENT / "horonom" / "capture"
        self.assertEqual(
            capture.confine_to_staging_parent(nested, what="--work-dir"),
            nested.resolve(),
        )


class RepoConfinementTest(unittest.TestCase):
    """`confine_to_repo` — the guard in front of `--out`."""

    def test_the_default_output_directory_is_accepted(self) -> None:
        self.assertEqual(
            capture.confine_to_repo(capture.DEFAULT_OUT, what="--out"),
            capture.DEFAULT_OUT,
        )

    def test_a_path_outside_the_repository_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            capture.confine_to_repo(pathlib.Path("/etc"), what="--out")
        self.assertIn("must be inside", str(caught.exception))

    def test_a_dot_dot_escape_is_refused(self) -> None:
        # Checked on the *resolved* path, so this is refused rather than being
        # accepted for starting with the repo root as a string.
        with self.assertRaises(SystemExit):
            capture.confine_to_repo(
                capture.REPO_ROOT / ".." / "elsewhere", what="--out"
            )

    def test_the_subject_of_the_complaint_is_named(self) -> None:
        # The message has to say which argument was wrong; "must be inside" on
        # its own sends the reader looking through four path arguments.
        with self.assertRaises(SystemExit) as caught:
            capture.confine_to_repo(pathlib.Path("/etc"), what="--work-dir")
        self.assertIn("--work-dir", str(caught.exception))


class HostStdinTest(unittest.TestCase):
    """The stdin handed to every provider during a capture."""

    def test_the_reported_working_directory_is_not_world_writable(self) -> None:
        # Providers may resolve project state relative to the cwd the host
        # reports. Naming a predictable directory under /tmp invites a provider
        # to read a project someone else planted there.
        import json

        payload = json.loads(capture.HOST_STDIN)
        for field in (payload["cwd"], payload["workspace"]["current_dir"]):
            self.assertEqual(field, str(capture.REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
