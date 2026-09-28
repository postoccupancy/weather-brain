from unittest.mock import MagicMock

import pytest

from app.frameworks.llamaindex.answering import ANSWER_PROMPT
from app.frameworks.llamaindex.runtime import POSTGRES_TEXT_TO_SQL
from app.planning.knowledge_paths import route_question
from app.retrieval.structured import generated_sql


@pytest.mark.parametrize("question,path", [
    ("What was the average temperature yesterday?", "sql"),
    ("Show latest readings", "sql"),
    ("How many readings on 2026-09-25?", "sql"),
    ("What is the recommended humidity?", "literature"),
    ("Explain relative humidity", "literature"),
    ("What is a temperature sensor?", "literature"),
    ("Is 80 F safe?", "literature"),
    ("Was my room too hot yesterday?", "sql+literature"),
    ("Compare today's readings with recommended thresholds", "sql+literature"),
    ("Are our humidity readings normal?", "sql+literature"),
    ("Tell me something", "sql"),
    ("tell me weather trends from January to March 2026", "sql"),
    ("TELL ME WEATHER TRENDS FROM JANUARY TO MARCH 2026", "sql"),
    ("Explain temperature trends", "sql"),
    ("What happened between February and April?", "sql"),
    ("Compare January and March 2026", "sql"),
    ("Pressure conditions", "sql"),
    ("Humidity averages and min/max", "sql"),
    ("Count measurements in 2025", "sql"),
    ("Weather for Jan to Mar", "sql"),
    ("Temperature", "sql"),
    ("What is humidity?", "literature"),
    ("What is current humidity?", "sql"),
    ("Define humidity", "literature"),
    ("How does a pressure sensor work?", "literature"),
    ("Find literature on humidity", "literature"),
    ("Research papers on temperature from 2026", "literature"),
    ("Summarize a study of indoor conditions", "literature"),
    ("What are WMO guidelines?", "literature"),
    ("Recommend an acceptable temperature range", "literature"),
    ("Compare sensor conditions against WMO standards", "sql+literature"),
    ("Compare sensor conditions with literature", "sql+literature"),
    ("Compare observed humidity with research", "sql+literature"),
    ("Explain how our readings compare with comfortable ranges", "sql+literature"),
    ("Were temperatures in January within acceptable ranges?", "sql+literature"),
])
def test_routes(question, path):
    assert route_question(question) == path


@pytest.mark.parametrize("statement", [
    "SELECT avg(temp_c) FROM readings;",
    "select * from readings where device_id = 'delete; drop table readings'",
    "SELECT ts AT TIME ZONE 'America/Los_Angeles' FROM readings",
    "SELECT 'it''s safe'", 'SELECT "temp_c" FROM "readings"',
    "```sql\nSELECT 1;\n```",
])
def test_select_validation_accepts_supported_reads(statement):
    assert generated_sql.validate_select(statement).lower().startswith("select")


@pytest.mark.parametrize("statement", [
    "DELETE FROM readings", "SELECT 1; DROP TABLE readings", "SELECT 1; SELECT 2",
    "SELECT * INTO backup FROM readings", "SELECT * FROM readings FOR UPDATE",
    "WITH changed AS (DELETE FROM readings RETURNING *) SELECT * FROM changed",
    "SELECT 1 -- comment", "SELECT /* comment */ 1", "SELECT $$x$$",
    "SELECT 'unterminated", "SELECT E'back\\slash'", "EXPLAIN SELECT 1", "", "SQLQuery: SELECT 1",
])
def test_select_validation_rejects_unsafe_or_unsupported_sql(statement):
    with pytest.raises(ValueError):
        generated_sql.validate_select(statement)


def test_execution_sets_read_only_before_generated_statement(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(generated_sql.psycopg, "connect", connect)
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"value": 1}]
    assert generated_sql.execute_select("SELECT 1") == [{"value": 1}]
    assert [c.args[0] for c in cursor.execute.call_args_list] == [
        "SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '30s'", "SELECT 1",
    ]
    connect.reset_mock()
    with pytest.raises(ValueError):
        generated_sql.execute_select("DROP TABLE readings")
    connect.assert_not_called()


def test_sql_prompt_has_calendar_range_timezone_guidance():
    prompt = POSTGRES_TEXT_TO_SQL.template
    assert "inclusive start timestamp and an exclusive end timestamp" in prompt
    assert "Do not cast ts to DATE or use BETWEEN for calendar periods" in prompt
    assert "ts >= '2026-01-01'::timestamp AT TIME ZONE 'America/Los_Angeles'" in prompt
    assert "AND ts < '2026-04-01'::timestamp AT TIME ZONE 'America/Los_Angeles'" in prompt
    assert "Apply the same boundary rule to other dates/ranges" in prompt


def test_january_through_march_timestamp_range_is_supported():
    statement = generated_sql.validate_select(
        "SELECT AVG(humidity) FROM readings "
        "WHERE ts >= '2026-01-01'::timestamp AT TIME ZONE 'America/Los_Angeles' "
        "AND ts < '2026-04-01'::timestamp AT TIME ZONE 'America/Los_Angeles'"
    )
    assert "'America/Los_Angeles'::DATE" not in statement
    assert " BETWEEN " not in statement.upper()
    assert statement.count("AT TIME ZONE 'America/Los_Angeles'") == 2


def test_synthesis_prompt_separates_data_and_literature_provenance():
    prompt = ANSWER_PROMPT.template
    assert "Facts derived from measured SQL results must use [data]" in prompt
    assert "[1] and [2] refer exclusively" in prompt
    assert "mark each part with its own provenance" in prompt
    assert "In literature-only answers" in prompt
    assert "do not invent [data] citations" in prompt
