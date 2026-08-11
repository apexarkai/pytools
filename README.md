# pytools

A small collection of standalone, dependency-free CLI utilities.

| Command | What it does |
|---|---|
| `find-dup` | Find duplicate files in a directory tree by size + SHA-256 checksum |
| `sub-text` | Substitute a regex pattern across files under a directory |

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development (linting/formatting):

```bash
pip install -e ".[dev]"
```

## `find-dup`

Recursively scans a directory, groups files by size, then confirms duplicates
with a cheap partial-hash prefilter before falling back to a full SHA-256
comparison — so large unique files are never fully re-read just to rule them out.

```
usage: find-dup [-h] [--min-size BYTES] dir_name

positional arguments:
  dir_name          Directory to scan. Supports '.', '~', and relative paths.

options:
  --min-size BYTES  Ignore files smaller than this size in bytes (default: 0).
```

Example:

```bash
$ find-dup ~/Downloads
Duplicate set (size: 2048576 bytes, sha256: 9f86d0...):
  /Users/apexarkai/Downloads/report.pdf
  /Users/apexarkai/Downloads/report (1).pdf

1 duplicate sets found, 1 redundant files, 2.0 MB reclaimable.
```

Symlinks are skipped (never followed). Unreadable files are reported as
warnings on stderr and skipped, without aborting the scan.

## `sub-text`

```
usage: sub-text [-d DIRECTORY] [-name FILEPATTERN] [-backup] pattern replacement
```

Substitutes a regex `pattern` with a literal `replacement` string across all
regular files under `-d` (default: current directory) matching the shell-style
`-name` glob (default: `*`). Pass `-backup` to keep a `.orig` copy of every
file it touches.

```bash
$ sub-text -d ./src -name '*.py' -backup 'foo' 'bar'
```

## Development

```bash
python -m unittest discover -s tests
black src tests
ruff check src tests
```
