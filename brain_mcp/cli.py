"""Command-line entry points for one-shot maintenance tasks."""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import vectors


def reindex() -> None:
    parser = argparse.ArgumentParser(
        prog="brain-reindex",
        description="Rebuild the vector index for the vault.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--full",
        action="store_true",
        help="Walk the entire vault and prune chunks for deleted notes.",
    )
    group.add_argument(
        "--note",
        metavar="ID",
        help="Reindex a single note by id (filename stem).",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Only reindex if the DB is empty (safe to run on every startup).",
    )
    args = parser.parse_args()

    t0 = time.time()
    if args.note:
        result = vectors.reindex_note(args.note)
    elif args.bootstrap:
        result = vectors.ensure_indexed() or {"status": "already_indexed"}
    else:
        result = vectors.reindex_all(prune=args.full)

    result["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
