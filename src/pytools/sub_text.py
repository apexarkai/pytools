import argparse
import fnmatch
import os
import re
import stat
import sys
import tempfile


def is_regular_file(path):
    """Mirrors `find -type f`: excludes symlinks, dirs, devices, etc."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _restore_after_failure(file, backup, exc):
    """Best-effort undo: put the untouched original back at its original
    path so a failed substitution leaves no trace, rather than leaving
    `file` missing and `file.orig` behind even when -backup wasn't asked
    for."""
    try:
        os.rename(backup, file)
    except OSError as restore_exc:
        print(
            f"Error processing {file} ({exc}); could not restore original "
            f"({restore_exc}); unmodified content preserved as {backup}"
        )
    else:
        print(f"Error processing {file} ({exc}); original restored, no changes made.")


def process_file(file, pattern, replacement, keep_backup):
    backup = file + ".orig"
    if os.path.exists(backup):
        print(
            f"Backup {backup} already exists, skipping {file} to avoid overwriting it."
        )
        return

    try:
        original_mode = stat.S_IMODE(os.stat(file).st_mode)
        os.rename(file, backup)
    except OSError as exc:
        print(f"Unable to back up {file} ({exc}), skipping...")
        return

    tmp_path = None
    try:
        with open(
            backup, encoding="utf-8", errors="surrogateescape", newline=""
        ) as backup_f:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(file) or ".", prefix=os.path.basename(file) + "."
            )
            with os.fdopen(
                tmp_fd, "w", encoding="utf-8", errors="surrogateescape", newline=""
            ) as out_f:
                # mkstemp() always creates files as mode 0600, ignoring
                # umask; restore the original file's permissions before it
                # goes live. fchmod on the already-open fd (rather than
                # os.chmod on the path) means the descriptor is owned by
                # the `with` block from the moment it's created, so a
                # failure here can't leak it.
                os.fchmod(out_f.fileno(), original_mode)
                for line in backup_f:
                    out_f.write(pattern.sub(replacement, line))
    except OSError as exc:
        _restore_after_failure(file, backup, exc)
        if tmp_path:
            os.unlink(tmp_path)
        return
    except Exception as exc:
        _restore_after_failure(file, backup, exc)
        if tmp_path:
            os.unlink(tmp_path)
        return

    os.replace(tmp_path, file)
    if not keep_backup:
        os.unlink(backup)


def main():
    parser = argparse.ArgumentParser(
        description="Substitute a regex pattern in files under a directory.",
    )
    parser.add_argument(
        "-d",
        dest="directory",
        default=".",
        help="Directory to search (defaults to current directory).",
    )
    parser.add_argument(
        "-name",
        dest="filepattern",
        default="*",
        help="Shell-style filename pattern (e.g. '*.html'). Defaults to '*'.",
    )
    parser.add_argument(
        "-backup",
        dest="backup",
        action="store_true",
        help="Back up each file with a '.orig' suffix.",
    )
    parser.add_argument("pattern", nargs="?", help="Regex to be substituted.")
    parser.add_argument("replacement", nargs="?", help="Replacement string.")

    args = parser.parse_args()

    if args.pattern is None or args.replacement is None:
        sys.stderr.write(
            f"""
Usage: {sys.argv[0]} [-d <directory>] [-name <filepattern>] [-backup] \
<pattern> <replacement>
       <directory> defaults to the current directory.
       <filename> can use shell syntax (e.g. '*.html').  It defaults to '*'.
       -backup causes each file to be backed up with a ".orig" suffix.
       <pattern> is the regex to be substituted.
       <replacement> is the string to replace <pattern> with.
"""
        )
        sys.exit(1)

    if not os.path.isdir(args.directory):
        sys.stderr.write(f"{sys.argv[0]}: {args.directory}: No such directory\n")
        sys.exit(1)

    try:
        pattern = re.compile(args.pattern)
    except re.error as exc:
        sys.stderr.write(f"Invalid pattern {args.pattern!r}: {exc}\n")
        sys.exit(1)

    # Neutralize re.sub's backslash templating (\n, \1, \g<name>, ...) so the
    # replacement text is inserted literally, matching the original Perl script.
    replacement = args.replacement.replace("\\", "\\\\")

    # Get list of pathnames, equivalent to:
    # find <directory> -type f -name '<filepattern>'
    filelist = []
    for root, _dirs, files in os.walk(args.directory):
        for name in files:
            if not fnmatch.fnmatch(name, args.filepattern):
                continue
            path = os.path.join(root, name)
            if is_regular_file(path):
                filelist.append(path)

    for file in filelist:
        process_file(file, pattern, replacement, args.backup)


if __name__ == "__main__":
    main()
