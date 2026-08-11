import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from pytools import find_dup


class ResolveDirTests(unittest.TestCase):
    def test_resolves_dot_to_cwd(self):
        result = find_dup.resolve_dir(".")
        self.assertEqual(result, Path.cwd().resolve())

    def test_expands_tilde(self):
        result = find_dup.resolve_dir("~")
        self.assertEqual(result, Path(os.path.expanduser("~")).resolve())

    def test_resolves_absolute_existing_dir(self):
        result = find_dup.resolve_dir("/tmp")
        self.assertEqual(result, Path("/tmp").resolve())

    def test_raises_for_nonexistent_path(self):
        with self.assertRaises(NotADirectoryError):
            find_dup.resolve_dir("/no/such/path/should/exist/anywhere")

    def test_raises_for_path_that_is_a_file(self):
        with self.assertRaises(NotADirectoryError):
            find_dup.resolve_dir(__file__)


class IterRegularFilesTests(unittest.TestCase):
    def test_finds_files_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a")
            sub = root / "sub"
            sub.mkdir()
            (sub / "b.txt").write_text("b")

            found = sorted(find_dup.iter_regular_files(root))

            self.assertEqual(found, sorted([root / "a.txt", sub / "b.txt"]))

    def test_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.txt"
            real.write_text("data")
            link = root / "link.txt"
            link.symlink_to(real)

            found = list(find_dup.iter_regular_files(root))

            self.assertEqual(found, [real])


class Sha256OfFileTests(unittest.TestCase):
    def test_matches_hashlib_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.bin"
            content = b"the quick brown fox jumps over the lazy dog" * 1000
            path.write_bytes(content)

            result = find_dup.sha256_of_file(path)

            self.assertEqual(result, hashlib.sha256(content).hexdigest())

    def test_small_chunk_size_still_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.bin"
            content = b"0123456789" * 5
            path.write_bytes(content)

            result = find_dup.sha256_of_file(path, chunk_size=3)

            self.assertEqual(result, hashlib.sha256(content).hexdigest())

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.bin"
            path.write_bytes(b"")

            result = find_dup.sha256_of_file(path)

            self.assertEqual(result, hashlib.sha256(b"").hexdigest())


class PartialSha256OfFileTests(unittest.TestCase):
    def test_matches_hashlib_of_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.bin"
            content = b"x" * 10_000
            path.write_bytes(content)

            result = find_dup.partial_sha256_of_file(path, chunk_size=4096)

            self.assertEqual(result, hashlib.sha256(content[:4096]).hexdigest())

    def test_shorter_than_chunk_size_hashes_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.bin"
            content = b"short content"
            path.write_bytes(content)

            result = find_dup.partial_sha256_of_file(path, chunk_size=4096)

            self.assertEqual(result, hashlib.sha256(content).hexdigest())


class FindDuplicatesTests(unittest.TestCase):
    def test_finds_duplicate_content_across_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("duplicate content")
            sub = root / "sub"
            sub.mkdir()
            (sub / "b.txt").write_text("duplicate content")
            (root / "unique.txt").write_text("only one of these")

            dup_sets, errors = find_dup.find_duplicates(root)

            self.assertEqual(errors, [])
            self.assertEqual(len(dup_sets), 1)
            size, checksum, paths = dup_sets[0]
            self.assertEqual(size, len(b"duplicate content"))
            self.assertEqual(paths, sorted([root / "a.txt", sub / "b.txt"]))

    def test_same_size_different_content_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("AAAAA")
            (root / "b.txt").write_text("BBBBB")

            dup_sets, errors = find_dup.find_duplicates(root)

            self.assertEqual(dup_sets, [])
            self.assertEqual(errors, [])

    def test_same_prefix_different_tail_beyond_partial_window_not_duplicate(self):
        # Regression check for the partial-hash prefilter: same size, same
        # first 4096+ bytes, but differing content after that point must
        # still NOT be reported as a duplicate.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_prefix = b"p" * 5000
            (root / "a.bin").write_bytes(shared_prefix + b"AAAA")
            (root / "b.bin").write_bytes(shared_prefix + b"BBBB")

            dup_sets, errors = find_dup.find_duplicates(root)

            self.assertEqual(dup_sets, [])
            self.assertEqual(errors, [])

    def test_finds_duplicates_larger_than_partial_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"z" * 9000
            (root / "a.bin").write_bytes(content)
            (root / "b.bin").write_bytes(content)

            dup_sets, errors = find_dup.find_duplicates(root)

            self.assertEqual(errors, [])
            self.assertEqual(len(dup_sets), 1)
            self.assertEqual(dup_sets[0][0], 9000)
            self.assertEqual(dup_sets[0][2], sorted([root / "a.bin", root / "b.bin"]))

    def test_empty_files_are_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"")
            (root / "b.txt").write_bytes(b"")

            dup_sets, errors = find_dup.find_duplicates(root)

            self.assertEqual(len(dup_sets), 1)
            self.assertEqual(dup_sets[0][0], 0)

    def test_unreadable_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("duplicate content")
            (root / "b.txt").write_text("duplicate content")
            blocked = root / "blocked.txt"
            blocked.write_text("duplicate content")
            blocked.chmod(0o000)

            try:
                dup_sets, errors = find_dup.find_duplicates(root)
            finally:
                blocked.chmod(0o644)  # allow tempdir cleanup

            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][0], blocked)
            # a.txt and b.txt still found as duplicates despite blocked.txt failing
            self.assertEqual(len(dup_sets), 1)
            self.assertEqual(dup_sets[0][2], sorted([root / "a.txt", root / "b.txt"]))

    def test_min_size_filters_out_smaller_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same")
            (root / "b.txt").write_text("same")

            dup_sets, errors = find_dup.find_duplicates(root, min_size=100)

            self.assertEqual(dup_sets, [])
            self.assertEqual(errors, [])


class FormatBytesTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(find_dup.format_bytes(0), "0 B")
        self.assertEqual(find_dup.format_bytes(1023), "1023 B")

    def test_kilobytes(self):
        self.assertEqual(find_dup.format_bytes(1024), "1.0 KB")

    def test_megabytes(self):
        self.assertEqual(find_dup.format_bytes(4 * 1024 * 1024 + 200_000), "4.2 MB")


class FormatReportTests(unittest.TestCase):
    def test_no_duplicates(self):
        self.assertEqual(find_dup.format_report([]), "No duplicates found.")

    def test_single_duplicate_set(self):
        paths = [Path("/tmp/a.txt"), Path("/tmp/b.txt")]
        report = find_dup.format_report([(1024, "abc123", paths)])

        self.assertIn("Duplicate set (size: 1024 bytes, sha256: abc123):", report)
        self.assertIn("  /tmp/a.txt", report)
        self.assertIn("  /tmp/b.txt", report)
        self.assertIn(
            "1 duplicate sets found, 1 redundant files, 1.0 KB reclaimable.", report
        )


class MainTests(unittest.TestCase):
    def test_reports_duplicates_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same content")
            (root / "b.txt").write_text("same content")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = find_dup.main([str(root)])

            self.assertEqual(exit_code, 0)
            self.assertIn("Duplicate set", stdout.getvalue())
            self.assertIn("1 duplicate sets found", stdout.getvalue())

    def test_no_duplicates_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("one")
            (root / "b.txt").write_text("two")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = find_dup.main([str(root)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), "No duplicates found.")

    def test_invalid_dir_returns_exit_code_1(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = find_dup.main(["/no/such/path/anywhere"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Not a directory", stderr.getvalue())

    def test_dot_alias_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same")
            (root / "b.txt").write_text("same")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = find_dup.main(["."])
            finally:
                os.chdir(cwd)

            self.assertEqual(exit_code, 0)
            self.assertIn("Duplicate set", stdout.getvalue())

    def test_min_size_flag_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same")
            (root / "b.txt").write_text("same")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = find_dup.main([str(root), "--min-size", "100"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), "No duplicates found.")


if __name__ == "__main__":
    unittest.main()
