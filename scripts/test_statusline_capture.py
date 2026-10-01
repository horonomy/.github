"""Path safety for the fixture capture driver (HORO-1635).

`statusline_capture.py` is the only script in this repo that clears a predictable
directory under a world-writable one, and it cannot stop doing so: `AF_UNIX` is
capped at 104 bytes on macOS and `$TMPDIR` there is long enough on its own to
blow the cap, so the staged daemons' socket root has to be short. That makes the
pre-created hostile directory the thing to defend against rather than the thing
to design away, and these are the proofs that it is defended.

The standard each case is held to is **fail closed with zero mutation**: a root
the script does not recognise as its own must produce a refusal *and* leave what
was there untouched. A refusal that has already deleted something is not a
refusal, which is why every negative case here plants a canary and asserts it
survived rather than only asserting that an exception was raised.

Nothing here runs the capture itself — that needs all three products installed.
These exercise the guards in front of it, which is the part that has to hold on
a machine where someone else got to `/tmp` first.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

import statusline_capture as capture


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
        # The attack this exists for: create `/tmp/hsc` first, pointing at
        # something of the victim's, and let their own script empty it.
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
