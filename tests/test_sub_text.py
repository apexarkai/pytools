import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pytools import sub_text


class IsRegularFileTests(unittest.TestCase):
    def test_regular_file_is_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("data")
            self.assertTrue(sub_text.is_regular_file(str(path)))

    def test_directory_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(sub_text.is_regular_file(tmp))

    def test_symlink_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.txt"
            real.write_text("data")
            link = Path(tmp) / "link.txt"
            link.symlink_to(real)
            self.assertFalse(sub_text.is_regular_file(str(link)))

    def test_nonexistent_path_is_false(self):
        self.assertFalse(sub_text.is_regular_file("/no/such/path/anywhere"))


class ProcessFileTests(unittest.TestCase):
    def test_substitutes_and_removes_backup_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello world")

            sub_text.process_file(str(path), re.compile("world"), "PYTOOLS", False)

            self.assertEqual(path.read_text(), "hello PYTOOLS")
            self.assertFalse((Path(tmp) / "a.txt.orig").exists())

    def test_keep_backup_preserves_original_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello world")

            sub_text.process_file(str(path), re.compile("world"), "PYTOOLS", True)

            self.assertEqual(path.read_text(), "hello PYTOOLS")
            backup = Path(tmp) / "a.txt.orig"
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(), "hello world")

    def test_preserves_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello world")
            path.chmod(0o741)

            sub_text.process_file(str(path), re.compile("world"), "PYTOOLS", False)

            self.assertEqual(path.stat().st_mode & 0o777, 0o741)

    def test_existing_backup_is_not_clobbered(self):
        # Regression check: a second -backup run over the same file must not
        # silently overwrite the first run's true-original backup with the
        # already-substituted intermediate content.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("CHANGED_ONCE")
            backup = Path(tmp) / "a.txt.orig"
            backup.write_text("ORIGINAL")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                sub_text.process_file(
                    str(path), re.compile("CHANGED_ONCE"), "CHANGED_TWICE", True
                )

            self.assertEqual(path.read_text(), "CHANGED_ONCE")
            self.assertEqual(backup.read_text(), "ORIGINAL")
            self.assertIn("already exists", stdout.getvalue())

    def test_restores_original_on_processing_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("important content")

            with mock.patch(
                "pytools.sub_text.tempfile.mkstemp",
                side_effect=OSError(28, "No space left on device"),
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    sub_text.process_file(
                        str(path), re.compile("important"), "REPLACED", False
                    )

            self.assertEqual(path.read_text(), "important content")
            self.assertFalse((Path(tmp) / "a.txt.orig").exists())
            self.assertIn("original restored", stdout.getvalue())

    def test_reports_when_restore_after_failure_also_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("important content")
            real_rename = os.rename

            def failing_restore_rename(src, dst):
                if src.endswith(".orig") and dst == str(path):
                    raise OSError(13, "Permission denied")
                return real_rename(src, dst)

            with (
                mock.patch(
                    "pytools.sub_text.tempfile.mkstemp",
                    side_effect=OSError(28, "No space left on device"),
                ),
                mock.patch(
                    "pytools.sub_text.os.rename", side_effect=failing_restore_rename
                ),
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    sub_text.process_file(
                        str(path), re.compile("important"), "REPLACED", False
                    )

            self.assertIn("could not restore", stdout.getvalue())
            self.assertTrue((Path(tmp) / "a.txt.orig").exists())

    def test_unable_to_back_up_is_reported_and_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello world")

            with mock.patch(
                "pytools.sub_text.os.rename",
                side_effect=OSError(13, "Permission denied"),
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    sub_text.process_file(
                        str(path), re.compile("world"), "PYTOOLS", False
                    )

            self.assertEqual(path.read_text(), "hello world")
            self.assertIn("Unable to back up", stdout.getvalue())

    def test_replacement_backslashes_are_literal_not_backreferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("foo")

            # main() neutralizes backslashes (each \ -> \\) before calling
            # process_file, so re.sub's \1-style backreference templating
            # never kicks in even though there's no capturing group here.
            # Passing the doubled form directly exercises that same re.sub
            # behavior without going through main().
            sub_text.process_file(str(path), re.compile("foo"), r"\\1bar", False)

            self.assertEqual(path.read_text(), r"\1bar")


class MainTests(unittest.TestCase):
    def test_substitutes_matching_files_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("hello world")
            (Path(tmp) / "b.html").write_text("hello world")

            exit_code = sub_text.main(["-d", tmp, "-name", "*.txt", "world", "PYTOOLS"])

            self.assertEqual(exit_code, 0)
            self.assertEqual((Path(tmp) / "a.txt").read_text(), "hello PYTOOLS")
            # non-matching file (filepattern) is left untouched
            self.assertEqual((Path(tmp) / "b.html").read_text(), "hello world")

    def test_recurses_into_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            (sub / "nested.txt").write_text("hello world")

            exit_code = sub_text.main(["-d", tmp, "world", "PYTOOLS"])

            self.assertEqual(exit_code, 0)
            self.assertEqual((sub / "nested.txt").read_text(), "hello PYTOOLS")

    def test_backup_flag_creates_orig_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("hello world")

            exit_code = sub_text.main(["-d", tmp, "-backup", "world", "PYTOOLS"])

            self.assertEqual(exit_code, 0)
            self.assertEqual((Path(tmp) / "a.txt.orig").read_text(), "hello world")

    def test_missing_pattern_and_replacement_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = sub_text.main(["-d", tmp])

            self.assertEqual(exit_code, 1)
            self.assertIn("Usage:", stderr.getvalue())

    def test_invalid_directory_returns_1(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = sub_text.main(["-d", "/no/such/path/anywhere", "foo", "bar"])

        self.assertEqual(exit_code, 1)
        self.assertIn("No such directory", stderr.getvalue())

    def test_invalid_regex_pattern_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = sub_text.main(["-d", tmp, "(unclosed", "bar"])

            self.assertEqual(exit_code, 1)
            self.assertIn("Invalid pattern", stderr.getvalue())

    def test_help_flag_exits_zero_without_requiring_pattern(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as cm:
            sub_text.main(["--help"])

        self.assertEqual(cm.exception.code, 0)
        self.assertIn("usage:", stdout.getvalue())

    def test_replacement_with_backreference_syntax_is_inserted_literally(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("foo")

            exit_code = sub_text.main(["-d", tmp, "foo", r"\1literal"])

            self.assertEqual(exit_code, 0)
            self.assertEqual((Path(tmp) / "a.txt").read_text(), r"\1literal")

    def test_symlinked_file_is_not_processed_as_its_own_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.txt"
            real.write_text("hello world")
            link = Path(tmp) / "link.txt"
            link.symlink_to(real)

            exit_code = sub_text.main(["-d", tmp, "world", "PYTOOLS"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(real.read_text(), "hello PYTOOLS")
            # link.txt itself must never be renamed/replaced by process_file
            # (that would silently turn it into a regular file); it stays a
            # symlink and reads through to real.txt's new content.
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.read_text(), "hello PYTOOLS")


if __name__ == "__main__":
    unittest.main()
