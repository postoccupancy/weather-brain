"""Inspect correction rebuild status; --apply retries one bounded daily chunk per pending correction."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from app.retrieval.structured.deployment_corrections import correction_status
from app.retrieval.vector.correction_rebuilds import process_correction_rebuilds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply:
        process_correction_rebuilds(node_id=args.node_id)
    print(json.dumps(correction_status(args.node_id),default=str,indent=2))


if __name__ == '__main__':
    main()
