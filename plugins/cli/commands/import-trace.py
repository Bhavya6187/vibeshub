#!/usr/bin/env python3
"""
Import a vibeshub trace as a resumable session for another CLI.

Usage:
  import-trace <trace-url-or-short-id> --to codex|claude [--checkout]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# The plugin root must be importable before the vibeshub_client import
# below. CLAUDE_PLUGIN_ROOT is set by Claude Code; fall back to this file's
# grandparent when the module is run directly.
_PLUGIN_ROOT = Path(
    os.environ.get(
        "CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent
    )
)
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from vibeshub_client.import_trace import (  # noqa: E402
    ImportTraceError,
    run_import,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="import-trace")
    parser.add_argument("ref", help="trace URL or short id")
    parser.add_argument(
        "--to", required=True, choices=("codex", "claude"), dest="target"
    )
    parser.add_argument("--checkout", action="store_true")
    args = parser.parse_args()
    server = os.environ.get("VIBESHUB_SERVER_URL", "https://vibeshub.ai")
    try:
        sys.exit(run_import(
            args.ref, args.target, server=server, cwd=os.getcwd(),
            checkout=args.checkout,
        ))
    # FileExistsError from place() is an OSError, and so is every other way
    # writing the session can fail (unwritable home, ENOSPC): report them all
    # as an import failure instead of a traceback.
    except (ImportTraceError, OSError) as e:
        print(f"import failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
