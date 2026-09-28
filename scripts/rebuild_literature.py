"""Rebuild the current PDF sources, retaining other sources; verify repeat ingestion.

Run from the repository: .venv/Scripts/python.exe scripts/rebuild_literature.py --apply
Writes a pre-change CSV backup and a JSON verification report under ignored scratch/.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import psycopg
from psycopg import sql

from app.database import DATABASE_URL
from app.frameworks.llamaindex import literature_ingestion as ingestion
from app.frameworks.llamaindex.answering import retrieve_literature
from app.providers.ollama.config import RAG_K, RAG_LITERATURE_TABLE

QUESTION = "According to the literature, what relative humidity range is recommended for indoor environments?"
TABLE = sql.Identifier("public", "data_" + RAG_LITERATURE_TABLE.lower())


def inspection():
    with psycopg.connect(DATABASE_URL, options="-c default_transaction_read_only=on -c statement_timeout=60000") as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT metadata_->>'source',count(*) FROM {} GROUP BY 1 ORDER BY 1").format(TABLE))
            counts = dict(cur.fetchall())
            cur.execute(sql.SQL("SELECT id,node_id,text,metadata_::text,embedding::text FROM {} ORDER BY id").format(TABLE))
            digest = hashlib.sha256()
            web = hashlib.sha256()
            for row in cur:
                encoded = json.dumps(row, ensure_ascii=False).encode()
                digest.update(encoded)
                if not json.loads(row[3]).get("source", "").lower().endswith(".pdf"):
                    web.update(encoded)
            return {"counts": counts, "total": sum(counts.values()),
                    "fingerprint": digest.hexdigest(), "non_pdf_fingerprint": web.hexdigest()}


def top_five():
    return [{"source": p["metadata"].get("source"), "page": p["metadata"].get("page_label"),
             "score": p["score"], "node_id": p["node_id"], "excerpt": p["text"][:220]}
            for p in retrieve_literature(QUESTION)]


def verify_prepared(rows, sources):
    # Match every prepared chunk occurrence, including equal text at different positions.
    def key(text, metadata):
        node = json.loads(metadata["_node_content"])
        return (metadata["source"], metadata.get("page_label"), text,
                node.get("start_char_idx"), node.get("end_char_idx"))
    expected = Counter(key(row[1], row[2].obj) for row in rows)
    with psycopg.connect(DATABASE_URL, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT text,metadata_ FROM {} WHERE metadata_->>'source'=ANY(%s)").format(TABLE), (sources,))
            actual = Counter(key(text, md) for text, md in cur.fetchall())
            assert actual == expected, "Stored chunk occurrences differ from prepared corpus"
            cur.execute(sql.SQL("SELECT count(*),count(DISTINCT node_id) FROM {} WHERE metadata_->>'source'=ANY(%s)").format(TABLE), (sources,))
            assert cur.fetchone() == (len(rows), len(rows))
    positions = {}
    for text, md in [(r[1], r[2].obj) for r in rows]:
        node = json.loads(md["_node_content"])
        positions.setdefault((md["source"], node.get("relationships", {}).get("1", {}).get("node_id")), []).append(
            (node.get("start_char_idx"), node.get("end_char_idx")))
    overlaps = sum(a[1] > b[0] for group in positions.values()
                   for a, b in zip(sorted(p for p in group if None not in p), sorted(p for p in group if None not in p)[1:]))
    assert overlaps > 0, "Expected distinct overlapping chunks"
    return {"matched_prepared_occurrences": len(rows), "overlapping_adjacent_chunks": overlaps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        parser.error("Use --apply to authorize transactional replacement and repeat verification")
    if urlsplit(DATABASE_URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("This maintenance command is restricted to the local database")
    if RAG_K != 5:
        raise SystemExit("Expected the existing RAG_K=5; configuration was not modified")
    folder = ROOT / "scratch" / ("literature-rebuild-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir(parents=True, exist_ok=False)
    report = {"question": QUESTION, "rag_k": RAG_K, "backup": str(folder / "before.csv")}
    def save(label, value):
        report[label] = value
        (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(label, json.dumps(value), flush=True)
    with psycopg.connect(DATABASE_URL, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur, (folder / "before.csv").open("wb") as backup:
            with cur.copy(sql.SQL("COPY {} TO STDOUT WITH (FORMAT CSV, HEADER TRUE)").format(TABLE)) as copy:
                for data in copy:
                    backup.write(data)
    save("before", inspection())
    save("top_five_before", top_five())
    paths = sorted(ingestion.PDF_DIR.rglob("*.pdf"))
    assert {p.name for p in paths} == {s for s in report["before"]["counts"] if s.lower().endswith(".pdf")}, "Review missing/new PDF sources before rebuilding"
    print("Preparing full PDF corpus (no database changes until all embeddings are ready)", flush=True)
    started = perf_counter()
    documents = ingestion.load_literature_documents(paths)
    rows, sources = ingestion.prepare_literature(documents)
    save("preparation", {"chunks": len(rows), "sources": sources, "seconds": perf_counter()-started})
    # Exercise a real failure after DELETE + one INSERT, and verify full rollback.
    original_insert = ingestion._insert_rows
    def fail_after_first(cursor, query, values):
        original_insert(cursor, query, values[:1])
        raise RuntimeError("verification rollback")
    ingestion._insert_rows = fail_after_first
    try:
        try:
            ingestion.replace_literature_sources(rows, sources)
        except RuntimeError as exc:
            assert str(exc) == "verification rollback"
        else:
            raise AssertionError("Failure injection did not fail")
    finally:
        ingestion._insert_rows = original_insert
    assert inspection() == report["before"], "Rollback did not preserve all original rows"
    save("rollback_verified", True)
    ingestion.replace_literature_sources(rows, sources)
    save("after_rebuild", inspection())
    save("prepared_verification", verify_prepared(rows, sources))
    single = next(p for p in paths if p.name == "M&V Protocol 2001.pdf")
    print("Re-ingesting one PDF", flush=True)
    save("single_ingestion", ingestion.ingest_literature_with_llamaindex(pdf_paths=[single]))
    save("after_single", inspection())
    assert report["after_single"]["counts"] == report["after_rebuild"]["counts"]
    print("Second complete PDF ingestion", flush=True)
    save("second_ingestion", ingestion.ingest_literature_with_llamaindex(pdf_paths=paths))
    save("after_second", inspection())
    assert report["after_second"]["counts"] == report["after_rebuild"]["counts"]
    assert report["after_second"]["non_pdf_fingerprint"] == report["before"]["non_pdf_fingerprint"]
    save("second_verification", verify_prepared(rows, sources))
    save("top_five_after", top_five())
    assert len(report["top_five_after"]) == 5
    save("complete", True)
    print("Report:", folder / "report.json", flush=True)


if __name__ == "__main__":
    main()
