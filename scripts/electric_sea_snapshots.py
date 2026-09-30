"""Preview bounded Electric Sea hourly documents; --apply atomically writes archive/vectors."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from app.retrieval.vector.snapshots import build_snapshot_records
from app.retrieval.vector.electric_sea_snapshots import store_electric_sea_snapshots
from app.providers.ollama.config import SNAPSHOT_DATA_TABLE, RAG_SNAPSHOT_TABLE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-id')
    parser.add_argument('--start', type=datetime.fromisoformat)
    parser.add_argument('--end', type=datetime.fromisoformat)
    parser.add_argument('--lookback-hours', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error('Historical bounds require both --start and --end')
    records = build_snapshot_records(source='signal_buckets', node_id=args.node_id,
        lookback_hours=args.lookback_hours, start_time=args.start, end_time=args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([dict(text=r.text, metadata=r.metadata) for r in records], indent=2), encoding='utf-8')
    print(f'Previewed {len(records)} snapshots: {args.output}')
    if args.apply:
        count = store_electric_sea_snapshots(records, archive=SNAPSHOT_DATA_TABLE, collection=RAG_SNAPSHOT_TABLE)
        print(f'Stored {count} snapshots in archive and vector collection')


if __name__ == '__main__':
    main()
