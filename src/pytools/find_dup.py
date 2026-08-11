"""Find duplicate files in a directory tree by size + SHA-256 checksum."""

import argparse
import hashlib
import os
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

CHUNK_SIZE = 65536
PARTIAL_CHUNK_SIZE = 4096  # ~one filesystem block; keeps the prefilter cheap


def resolve_dir(dir_arg: str) -> Path:
    expanded = os.path.expanduser(dir_arg)
    resolved = Path(expanded).resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(
            f"Not a directory: {dir_arg!r} (resolved to {resolved})"
        )
    return resolved


def _iter_regular_file_entries(
    root: Path, errors: list[tuple[Path, OSError]]
) -> Iterator[tuple[Path, int]]:
    """Yield (path, size) for every regular file under root, recursively.

    Skips symlinks (and does not descend into symlinked directories).
    Uses os.scandir's cached DirEntry so each file costs ~1 stat/lstat
    syscall instead of the 3 that separate is_symlink/is_file/stat calls
    would cost. Any OSError encountered while scanning a directory or
    stat'ing an entry is appended to errors and that entry is skipped,
    without aborting the rest of the walk.
    """
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            scanner = os.scandir(current)
        except OSError as e:
            errors.append((Path(current), e))
            continue
        with scanner:
            for entry in scanner:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        yield Path(entry.path), size
                except OSError as e:
                    errors.append((Path(entry.path), e))


def iter_regular_files(root: Path) -> Iterator[Path]:
    errors: list[tuple[Path, OSError]] = []
    for path, _size in _iter_regular_file_entries(root, errors):
        yield path


def sha256_of_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def partial_sha256_of_file(path: Path, chunk_size: int = PARTIAL_CHUNK_SIZE) -> str:
    """Hash only the first chunk_size bytes — a cheap prefilter to reject
    same-size files that differ early, without reading the whole file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        hasher.update(f.read(chunk_size))
    return hasher.hexdigest()


def find_duplicates(
    root: Path,
    min_size: int = 0,
) -> tuple[list[tuple[int, str, list[Path]]], list[tuple[Path, OSError]]]:
    errors: list[tuple[Path, OSError]] = []
    size_groups: dict[int, list[Path]] = defaultdict(list)

    for path, size in _iter_regular_file_entries(root, errors):
        if size < min_size:
            continue
        size_groups[size].append(path)

    duplicate_sets: list[tuple[int, str, list[Path]]] = []
    for size, paths in size_groups.items():
        if len(paths) < 2:
            continue

        # Files no larger than the partial-hash window get no benefit from
        # a separate prefilter (it would just re-read the whole file), so
        # they go straight to the full-hash pass below.
        if size <= PARTIAL_CHUNK_SIZE:
            candidate_groups = [paths]
        else:
            partial_groups: dict[str, list[Path]] = defaultdict(list)
            for path in paths:
                try:
                    partial_checksum = partial_sha256_of_file(path)
                except OSError as e:
                    errors.append((path, e))
                    continue
                partial_groups[partial_checksum].append(path)
            candidate_groups = [
                group for group in partial_groups.values() if len(group) >= 2
            ]

        for group in candidate_groups:
            checksum_groups: dict[str, list[Path]] = defaultdict(list)
            for path in group:
                try:
                    checksum = sha256_of_file(path)
                except OSError as e:
                    errors.append((path, e))
                    continue
                checksum_groups[checksum].append(path)
            for checksum, group_paths in checksum_groups.items():
                if len(group_paths) >= 2:
                    duplicate_sets.append((size, checksum, sorted(group_paths)))

    duplicate_sets.sort(key=lambda entry: (entry[0], entry[1]))
    return duplicate_sets, errors


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if unit == "B":
            if size < 1024:
                return f"{int(size)} {unit}"
        elif size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_report(duplicate_sets: list[tuple[int, str, list[Path]]]) -> str:
    if not duplicate_sets:
        return "No duplicates found."

    lines: list[str] = []
    total_redundant = 0
    total_reclaimable = 0
    for size, checksum, paths in duplicate_sets:
        lines.append(f"Duplicate set (size: {size} bytes, sha256: {checksum}):")
        for path in paths:
            lines.append(f"  {path}")
        lines.append("")
        total_redundant += len(paths) - 1
        total_reclaimable += (len(paths) - 1) * size

    lines.append(
        f"{len(duplicate_sets)} duplicate sets found, {total_redundant} redundant "
        f"files, {format_bytes(total_reclaimable)} reclaimable."
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find duplicate files in a directory by size + SHA-256 checksum."
    )
    parser.add_argument(
        "dir_name",
        help="Directory to scan. Supports '.', '~', and relative paths.",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=0,
        metavar="BYTES",
        help="Ignore files smaller than this size in bytes (default: 0).",
    )
    args = parser.parse_args(argv)

    try:
        root = resolve_dir(args.dir_name)
    except NotADirectoryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    duplicate_sets, errors = find_duplicates(root, min_size=args.min_size)

    for path, error in errors:
        print(f"Warning: skipping {path}: {error}", file=sys.stderr)

    print(format_report(duplicate_sets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
