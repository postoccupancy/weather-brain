"""Conservative single-SELECT validation and PostgreSQL read-only enforcement."""
import re

import psycopg
from psycopg.rows import dict_row

from app.database import DATABASE_URL


_TOKEN = re.compile(r"\s+|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9$]*|[0-9]+|[(),.*+/<>=!%:;\[\]|^-]")
_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "INTO", "CREATE", "ALTER", "DROP",
    "TRUNCATE", "COPY", "CALL", "DO", "EXECUTE", "GRANT", "REVOKE", "LOCK",
    "SET", "RESET", "VACUUM", "ANALYZE", "REFRESH", "FOR",
}


def validate_select(generated: str) -> str:
    statement = generated.strip()
    if statement.startswith("```"):
        match = re.fullmatch(r"```(?:sql)?\s*\n(.*?)\n```", statement, re.I | re.S)
        if not match:
            raise ValueError("Expected a single SELECT statement")
        statement = match.group(1).strip()
    tokens = []
    position = 0
    while position < len(statement):
        match = _TOKEN.match(statement, position)
        if match is None:
            raise ValueError("Unsupported SQL syntax")
        token = match.group()
        if token.startswith("'") and "\\" in token:
            raise ValueError("Backslash string escapes are not supported")
        position = match.end()
        if not token.isspace():
            tokens.append(token)
    if not tokens or tokens[0].upper() != "SELECT":
        raise ValueError("Only SELECT is allowed")
    if tokens[-1] == ";":
        tokens.pop()
        statement = statement.rstrip().removesuffix(";").rstrip()
    if ";" in tokens or any(t.upper() in _FORBIDDEN for t in tokens):
        raise ValueError("Only a single read-only SELECT is allowed")
    if any(a + b in {"--", "/*", "*/"} for a, b in zip(tokens, tokens[1:])):
        raise ValueError("SQL comments are not supported")
    return statement


def execute_select(statement: str) -> list[dict]:
    statement = validate_select(statement)
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute(statement)
            return cursor.fetchall()
