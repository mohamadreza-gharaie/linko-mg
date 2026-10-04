"""
PostgreSQL database layer for Linko.

The application still uses a small DB-API shaped interface (`get_db().execute(...)`)
so the rest of the Flask app can stay simple. DATABASE_URL is supplied by Render.
"""

import os
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Linko now requires a PostgreSQL database. "
        "On Render, connect the web service to the Linko Postgres database."
    )


def _normalize_url(url: str) -> str:
    # Render's internal connection string normally needs no SSL parameter.
    # If a user supplies an external URL without sslmode, require SSL.
    parts = urlparse(url)
    query = parse_qs(parts.query)
    if parts.hostname and parts.hostname.endswith(".render.com") and "sslmode" not in query:
        query["sslmode"] = ["require"]
        parts = parts._replace(query=urlencode(query, doseq=True))
        return urlunparse(parts)
    return url


class CursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _translate_sql(sql: str) -> str:
    # SQLite parameter markers -> PostgreSQL.
    sql = sql.replace("?", "%s")

    # SQLite's INSERT OR IGNORE -> PostgreSQL's equivalent.
    sql = re.sub(
        r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
        "INSERT INTO",
        sql,
        flags=re.I,
    )
    if re.search(r"\bINSERT\s+INTO\b", sql, re.I) and re.search(r"\bINSERT\s+OR\s+IGNORE\b", sql, re.I):
        # Kept for completeness; the normal replacement above handles it.
        sql += " ON CONFLICT DO NOTHING"

    # The replacement above cannot know whether a statement was OR IGNORE after
    # replacement, so detect the original form through a marker-free second pass
    # is not possible here. Handle it explicitly before the generic replacement.
    return sql


def _prepare_sql(sql: str) -> str:
    was_insert_ignore = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql, re.I))
    sql = sql.replace("?", "%s")
    if was_insert_ignore:
        sql = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            sql,
            flags=re.I,
        )
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    sql = re.sub(r"\bdatetime\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    return sql


class ConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(_prepare_sql(sql), params)
        return CursorWrapper(cur)

    def cursor(self):
        return CursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db():
    conn = psycopg.connect(_normalize_url(DATABASE_URL), row_factory=dict_row)
    return ConnectionWrapper(conn)


def init_db():
    """Create all persistent Linko tables if they do not already exist."""
    conn = get_db()
    try:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                avatar_color TEXT NOT NULL DEFAULT '#4e89ff',
                avatar_url TEXT,
                bio TEXT,
                last_seen TIMESTAMPTZ,
                theme_color TEXT NOT NULL DEFAULT 'blue',
                theme_mode TEXT NOT NULL DEFAULT 'dark',
                notifications_enabled INTEGER NOT NULL DEFAULT 1,
                archive_password_hash TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chats (
                id BIGSERIAL PRIMARY KEY,
                type TEXT NOT NULL CHECK(type IN ('private', 'group', 'channel')),
                name TEXT,
                description TEXT,
                avatar_url TEXT,
                is_public INTEGER NOT NULL DEFAULT 0,
                open_chat INTEGER NOT NULL DEFAULT 0,
                owner_id BIGINT REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner', 'admin', 'member')),
                archived INTEGER NOT NULL DEFAULT 0,
                last_read_message_id BIGINT NOT NULL DEFAULT 0,
                joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL DEFAULT '',
                message_type TEXT NOT NULL DEFAULT 'text',
                file_url TEXT,
                file_name TEXT,
                file_size BIGINT,
                reply_to_id BIGINT,
                edited_at TIMESTAMPTZ,
                forwarded_from_name TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS message_reactions (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                emoji TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_pins (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                pinned_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                pinned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS saved_items (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL DEFAULT '',
                message_type TEXT NOT NULL DEFAULT 'text',
                file_url TEXT,
                file_name TEXT,
                file_size BIGINT,
                forwarded_from_name TEXT,
                edited_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS polls (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                chat_id BIGINT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                creator_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                question TEXT NOT NULL,
                poll_type TEXT NOT NULL DEFAULT 'regular',
                correct_option_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS poll_options (
                id BIGSERIAL PRIMARY KEY,
                poll_id BIGINT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                option_text TEXT NOT NULL,
                option_order INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS poll_votes (
                id BIGSERIAL PRIMARY KEY,
                poll_id BIGINT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                option_id BIGINT NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                voted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(poll_id, user_id)
            )
            """,
        ]
        for statement in statements:
            conn._conn.execute(statement)

        # PostgreSQL-specific indexes for the queries used heavily by Linko.
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_chat_members_user ON chat_members(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id)",
            "CREATE INDEX IF NOT EXISTS idx_reactions_message ON message_reactions(message_id)",
            "CREATE INDEX IF NOT EXISTS idx_pins_chat ON chat_pins(chat_id)",
            "CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_items(user_id, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_poll_options_poll ON poll_options(poll_id, option_order)",
            "CREATE INDEX IF NOT EXISTS idx_poll_votes_poll ON poll_votes(poll_id)",
        ]
        for statement in indexes:
            conn._conn.execute(statement)

        # Migrate databases created by older Linko versions without touching
        # existing data. These columns are personal/user-level archive state.
        migrations = [
            ("users", "archive_password_hash", "TEXT"),
            ("chat_members", "archived", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for table_name, column_name, column_type in migrations:
            exists = conn._conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s AND column_name=%s",
                (table_name, column_name),
            ).fetchone()
            if not exists:
                conn._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

        conn.commit()
    finally:
        conn.close()
