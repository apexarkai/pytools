# pytools

- `black`/`ruff` dev deps are pinned to exact versions in pyproject.toml,
  not `>=` ranges. black 26.x dropped Python 3.9 support and changed
  stable-style formatting vs 25.11.0 — an unpinned range let pip resolve
  different black versions per Python in the CI matrix, so `--check`
  disagreed with itself across jobs. Bump the pin deliberately, not by
  loosening it.
- CLI entry points (`find-dup`, `sub-text`) are installed via
  `.venv/bin/pip install -e ".[dev]"` then symlinked from `.venv/bin/`
  into `~/bin` (already on PATH ahead of `/usr/local/bin`). After editing
  source, just reinstall in the venv — no need to re-symlink.
