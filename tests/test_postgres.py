from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import sqlalchemy_database_url
from app.retrieval.structured import sql_queries as db


@pytest.fixture
def cursor(monkeypatch):
    cursor = MagicMock()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://test@localhost/test")
    monkeypatch.setattr(db.psycopg, "connect", MagicMock(return_value=connection))
    return cursor


@pytest.mark.parametrize("table,time_column", [(db.RAW_DATA_TABLE, "ts"), (db.SNAPSHOT_DATA_TABLE, "window_start")])
@pytest.mark.parametrize("descending", [True, False])
def test_read_filters_order_and_pagination(cursor, table, time_column, descending):
    cursor.fetchall.return_value = [{"device_id": "sensor"}]
    device = "sensor'; DROP TABLE readings; --"
    result = db.get_postgres(table=table, device_id=device, start_ts="2026-01-01Z",
                             end_ts="2026-02-01Z", limit=7, offset=4, order_desc=descending)
    query, params = cursor.execute.call_args.args
    statement = query.as_string()
    assert f'FROM "{table}"' in statement
    assert f'"{time_column}" >= %s::timestamptz' in statement
    assert f'"{time_column}" <= %s::timestamptz' in statement
    assert f'ORDER BY "{time_column}" {"DESC" if descending else "ASC"}' in statement
    assert params == [device, "2026-01-01Z", "2026-02-01Z", 7, 4]
    assert device not in statement
    assert result == [{"device_id": "sensor"}]


def test_insert_uses_parameters_and_committing_context(cursor):
    row = {"device_id": "sensor'", "temp_c": 23.5}
    cursor.fetchall.return_value = [row]
    assert db.insert_postgres(row).data == [row]
    query, params = cursor.execute.call_args.args
    assert '"device_id", "temp_c"' in query.as_string()
    assert "RETURNING *" in query.as_string()
    assert params == list(row.values())
    db.psycopg.connect.return_value.__exit__.assert_called_once_with(None, None, None)


def test_rejected_table_and_missing_configuration_do_not_connect(cursor, monkeypatch):
    assert db.get_postgres(table="readings; DROP TABLE readings") == []
    assert db.get_postgres_aggregated(table="bad", bucket_seconds=60) == []
    assert db.get_postgres_summary(table="bad") == {}
    monkeypatch.setattr(db, "DATABASE_URL", "")
    assert db.insert_postgres({"device_id": "sensor"}) is None
    assert db.get_postgres() == []
    assert db.get_postgres_aggregated(bucket_seconds=60) == []
    assert db.get_postgres_summary() == {}
    db.psycopg.connect.assert_not_called()


def test_database_errors_preserve_empty_results(cursor):
    cursor.execute.side_effect = RuntimeError("unavailable")
    assert db.insert_postgres({"device_id": "sensor"}) is None
    assert db.get_postgres() == []
    assert db.get_postgres_aggregated(bucket_seconds=60) == []
    assert db.get_postgres_summary() == {}


@pytest.mark.parametrize("mode", ["full", "lite"])
def test_aggregation_and_summary_keep_filters(cursor, mode):
    cursor.fetchall.return_value = [{"count": 2}]
    assert db.get_postgres_aggregated(bucket_seconds=60, device_id="sensor", aggregate_mode=mode) == [{"count": 2}]
    query, params = cursor.execute.call_args.args
    assert "date_bin" in query.as_string()
    assert params["bucket_seconds"] == 60
    assert params["device_id"] == "sensor"
    assert ("min(temp_c)" in query.as_string()) == (mode == "full")
    cursor.fetchone.return_value = {"count": 2}
    assert db.get_postgres_summary(device_id="sensor") == {"count": 2}


def test_shared_url_preserves_credentials_and_options():
    derived = sqlalchemy_database_url("postgresql://postgres:p%40ss%2Fword@localhost:5432/postgres?sslmode=prefer&options=-cstatement_timeout%3D5000")
    url = make_url(derived)
    assert url.password == "p@ss/word"
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "prefer"
    assert url.query["options"] == "-cstatement_timeout=5000 -csearch_path=public,extensions"
    assert create_engine(derived).dialect.driver == "psycopg"
    assert create_async_engine(derived).dialect.is_async


def test_missing_url_is_explicit():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        sqlalchemy_database_url("")


def test_raw_rows_keep_json_compatible_values(cursor):
    from datetime import datetime, timezone
    from decimal import Decimal
    from uuid import UUID

    cursor.fetchall.return_value = [{"ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    "temp_c": Decimal("23.5"), "id": UUID(int=1)}]
    assert db.get_postgres() == [{"ts": "2026-01-01T00:00:00+00:00", "temp_c": 23.5,
                                  "id": "00000000-0000-0000-0000-000000000001"}]


def test_ping_reports_configuration_without_connecting(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "DATABASE_URL", "postgresql://test@localhost/test")
    connect = MagicMock(side_effect=AssertionError("Health check must not connect"))
    monkeypatch.setattr(db.psycopg, "connect", connect)
    assert main.ping() == {"pong": True, "database_configured": True}
    connect.assert_not_called()


@pytest.mark.parametrize("configured,result,detail", [
    (True, db.InsertResult([{"id": 1}]), None),
    (True, None, "Database write failed"),
    (True, db.InsertResult([]), "Database write failed"),
    (False, None, "Database is not configured"),
])
def test_ingest_postgres_status(monkeypatch, configured, result, detail):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setenv("INGEST_TOKEN", "test-token")
    monkeypatch.setattr(main, "DATABASE_URL", "postgresql://test@localhost/test" if configured else "")
    insert = MagicMock(return_value=result)
    monkeypatch.setattr(main, "insert_postgres", insert)
    previous_reading = {"device_id": "previous", "temp_c": 20}
    monkeypatch.setattr(main, "latest_reading", previous_reading)
    # No lifespan context: the existing background indexer must not run in tests.
    client = TestClient(main.app)
    response = client.post("/ingest", json={"device_id": "sensor", "temp_c": 23.5},
                           headers={"X-Ingest-Token": "test-token"})
    if detail is None:
        assert response.status_code == 200
        assert response.json() == {"ok": True, "postgres": "ok"}
        assert main.latest_reading["temp_c"] == 23.5
    else:
        assert response.status_code == 503
        assert response.json() == {"ok": False, "postgres": "error", "detail": detail}
        assert main.latest_reading is previous_reading
    assert insert.call_count == int(configured)


def test_llamaindex_preserves_table_mapping_without_setup(monkeypatch):
    from app.frameworks.llamaindex import runtime, vector_indexing
    url = sqlalchemy_database_url("postgresql://postgres:dummy@localhost/postgres")
    monkeypatch.setattr(vector_indexing, "sqlalchemy_database_url", lambda: url)
    monkeypatch.setattr(vector_indexing, "RAG_SNAPSHOT_TABLE", "rag_snapshots")
    store = vector_indexing._snapshot_vector_store()
    assert store._table_class.__tablename__ == "data_rag_snapshots"
    assert store.embed_dim == 768
    assert store.perform_setup is False
    assert store.connection_string == store.async_connection_string == url
    monkeypatch.setattr(runtime, "sqlalchemy_database_url", lambda: url)
    monkeypatch.setattr(runtime, "RAG_LITERATURE_TABLE", "rag_literature_chunks")
    factory = MagicMock()
    monkeypatch.setattr(runtime.PGVectorStore, "from_params", factory)
    monkeypatch.setattr(runtime.StorageContext, "from_defaults", MagicMock())
    monkeypatch.setattr(runtime.VectorStoreIndex, "from_vector_store", MagicMock())
    runtime.get_vector_index_from_postgres.cache_clear()
    runtime.get_literature_store.cache_clear()
    runtime.get_vector_index_from_postgres()
    assert factory.call_args.kwargs["table_name"] == "rag_literature_chunks"
    assert factory.call_args.kwargs["perform_setup"] is False
    runtime.get_vector_index_from_postgres.cache_clear()
    runtime.get_literature_store.cache_clear()


def test_langchain_initialization_does_not_create_schema_or_collections(monkeypatch):
    from app.frameworks.langchain.postgres import ExistingPGVector
    session = MagicMock()

    @contextmanager
    def fake_session(self):
        yield session

    monkeypatch.setattr(ExistingPGVector, "_make_sync_session", fake_session)
    monkeypatch.setattr(ExistingPGVector, "get_collection", lambda self, session: object())
    extension = MagicMock(side_effect=AssertionError("Unexpected DDL"))
    monkeypatch.setattr(ExistingPGVector, "create_vector_extension", extension)
    store = ExistingPGVector(embeddings=MagicMock(), collection_name="rag_snapshots",
                             connection=sqlalchemy_database_url("postgresql://postgres:dummy@localhost/postgres"),
                             create_extension=False)
    assert store.EmbeddingStore.__tablename__ == "langchain_pg_embedding"
    assert store.CollectionStore.__tablename__ == "langchain_pg_collection"
    session.commit.assert_not_called()
    extension.assert_not_called()
    monkeypatch.setattr(ExistingPGVector, "get_collection", lambda self, session: None)
    with pytest.raises(RuntimeError, match="Existing vector collection not found"):
        store.create_collection()
