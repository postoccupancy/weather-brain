"""Explicit node/deployment/time-scoped backfill; dry-run unless --apply is given."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from app.retrieval.structured.deployments import backfill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--deployment-id', required=True, type=UUID)
    parser.add_argument('--start', required=True, type=datetime.fromisoformat)
    parser.add_argument('--end', required=True, type=datetime.fromisoformat)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        count = backfill(args.node_id, args.deployment_id, args.start, args.end, apply=args.apply)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"{'Changed' if args.apply else 'Would change'} {count} unassigned signal buckets")


if __name__ == '__main__':
    main()
