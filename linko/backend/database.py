import sqlite3
import os

DATA_DIR = os.environ.get("LINKO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "chat.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            avatar_color TEXT NOT NULL DEFAULT '#4e89ff',
            avatar_url TEXT,
            bio TEXT,
            theme_color TEXT NOT NULL DEFAULT 'blue',
            theme_mode TEXT NOT NULL DEFAULT 'dark',
            notifications_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ('private', 'group', 'channel')),
            name TEXT,
            description TEXT,
            avatar_url TEXT,
            is_public INTEGER NOT NULL DEFAULT 0,
            open_chat INTEGER NOT NULL DEFAULT 0,
            owner_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner', 'admin', 'member')),
            last_read_message_id INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (chat_id, user_id),
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            message_type TEXT NOT NULL DEFAULT 'text',
            file_url TEXT,
            file_name TEXT,
            file_size INTEGER,
            reply_to_id INTEGER,
            edited_at TEXT,
            forwarded_from_name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS message_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(message_id, user_id),
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            pinned_by INTEGER NOT NULL,
            pinned_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(chat_id, message_id),
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (pinned_by) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Saved Messages: a private notes-to-self space, deliberately NOT modeled as a
    # regular chat (no membership, no real-time sync needed) — only its owner can
    # ever see these rows, so it's simplest and safest as its own standalone table.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saved_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            message_type TEXT NOT NULL DEFAULT 'text',
            file_url TEXT,
            file_name TEXT,
            file_size INTEGER,
            forwarded_from_name TEXT,
            edited_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Polls & quizzes (group/channel only). A poll "rides" on top of a normal
    # messages row (message_type='poll') so it gets pagination, reactions, pins,
    # read receipts etc. for free; the tables below hold its extra structured data.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL UNIQUE,
            chat_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            poll_type TEXT NOT NULL DEFAULT 'regular',
            correct_option_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS poll_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id INTEGER NOT NULL,
            option_text TEXT NOT NULL,
            option_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (poll_id) REFERENCES polls(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS poll_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id INTEGER NOT NULL,
            option_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            voted_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(poll_id, user_id),
            FOREIGN KEY (poll_id) REFERENCES polls(id) ON DELETE CASCADE,
            FOREIGN KEY (option_id) REFERENCES poll_options(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.commit()

    # ---- Lightweight migration for databases created by an older version ----
    _ensure_column(conn, "users", "avatar_url", "TEXT")
    _ensure_column(conn, "users", "bio", "TEXT")
    _ensure_column(conn, "users", "last_seen", "TEXT")
    _ensure_column(conn, "users", "theme_color", "TEXT NOT NULL DEFAULT 'blue'")
    _ensure_column(conn, "users", "theme_mode", "TEXT NOT NULL DEFAULT 'dark'")
    _ensure_column(conn, "users", "notifications_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "chats", "open_chat", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chats", "avatar_url", "TEXT")
    _ensure_column(conn, "messages", "message_type", "TEXT NOT NULL DEFAULT 'text'")
    _ensure_column(conn, "messages", "file_url", "TEXT")
    _ensure_column(conn, "messages", "file_name", "TEXT")
    _ensure_column(conn, "messages", "file_size", "INTEGER")
    _ensure_column(conn, "messages", "reply_to_id", "INTEGER")
    _ensure_column(conn, "messages", "edited_at", "TEXT")
    _ensure_column(conn, "messages", "forwarded_from_name", "TEXT")
    _ensure_column(conn, "chat_members", "last_read_message_id", "INTEGER NOT NULL DEFAULT 0")

    conn.close()


def _ensure_column(conn, table, column, definition):
    """Add a column to an existing table if it doesn't already exist.
    Lets old chat.db files created by a previous version of the app keep working."""
    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
