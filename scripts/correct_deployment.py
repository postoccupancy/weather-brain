"""Preview an explicit historical transition. Applying requires the saved preview token."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from app.retrieval.structured.deployment_corrections import correct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--old-deployment-id', required=True, type=UUID)
    parser.add_argument('--new-deployment-id', required=True, type=UUID)
    parser.add_argument('--boundary', required=True, type=datetime.fromisoformat)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--preview-token')
    args = parser.parse_args()
    if args.apply != bool(args.preview_token):
        parser.error('--apply requires --preview-token from the reviewed dry run; omit both for preview')
    try:
        result = correct(args.node_id, args.old_deployment_id, args.new_deployment_id,
                         args.boundary, args.reason, preview_token=args.preview_token)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
