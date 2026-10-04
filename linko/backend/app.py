import os
import random
import re
import uuid
import mimetypes
import time
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from flask import Flask, request, session, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from backend.database import get_db, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

app.secret_key = os.environ.get("SECRET_KEY", "please-change-this-secret-key-in-production")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Keep people logged in across browser restarts, so they only need to sign in once.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=90)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 مگابایت سقف حجم هر فایل

# PostgreSQL is the durable store. Uploaded media deliberately lives only on the
# web service filesystem and is therefore disposable on restart/sleep.
def cleanup_ephemeral_media():
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Remove files from the current ephemeral filesystem.
    for name in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    # Media metadata is disposable too. Keep the durable message/user/chat data,
    # but do not leave broken links to files that no longer exist.
    db = get_db()
    try:
        db.execute(
            "DELETE FROM messages WHERE message_type IN ('image', 'video', 'voice', 'file')"
        )
        db.execute(
            "DELETE FROM saved_items WHERE message_type IN ('image', 'video', 'voice', 'file')"
        )
        # Avatars are also images, so profile/chat avatar files are ephemeral.
        db.execute("UPDATE users SET avatar_url=NULL")
        db.execute("UPDATE chats SET avatar_url=NULL")
        db.commit()
    finally:
        db.close()


# Initialize PostgreSQL tables before Gunicorn begins serving requests.
init_db()
cleanup_ephemeral_media()

CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                     max_http_buffer_size=60 * 1024 * 1024)


def _socket_json_safe(value):
    """Convert PostgreSQL values such as datetime into Socket.IO JSON-safe values."""
    from datetime import date, datetime

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _socket_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_socket_json_safe(item) for item in value]
    return value


_original_socketio_emit = socketio.emit

def _safe_socketio_emit(event, data=None, *args, **kwargs):
    return _original_socketio_emit(
        event, _socket_json_safe(data), *args, **kwargs
    )


# PostgreSQL returns TIMESTAMPTZ values as Python datetime objects.
# Flask's jsonify handles those values, but Socket.IO's JSON encoder does not.
# Normalize every Socket.IO payload in one place so real-time updates never fail
# just because a payload contains last_seen/created_at/pinned_at/etc.
socketio.emit = _safe_socketio_emit

AVATAR_COLORS = ["#4e89ff", "#ff6b6b", "#2ecc71", "#f39c12", "#9b59b6",
                  "#1abc9c", "#e74c3c", "#3498db", "#e67e22", "#16a085"]

THEME_COLOR_PRESETS = {"blue", "purple", "green", "teal", "orange", "red", "pink", "indigo"}
THEME_MODES = {"dark", "light"}
ALLOWED_REACTIONS = {"👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "🎉"}

ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp",              # image
    "mp4", "webm", "mov", "mkv", "avi",                       # video
    "mp3", "wav", "ogg", "m4a", "aac", "weba",                # audio / voice
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",       # documents
    "zip", "rar", "7z", "txt", "csv", "json",                 # misc files
}


def guess_message_type(mimetype, ext):
    mimetype = mimetype or ""
    if mimetype.startswith("image/"):
        return "image"
    if mimetype.startswith("video/"):
        return "video"
    if mimetype.startswith("audio/"):
        return "voice"
    # fall back to extension-based guessing when the browser sends a generic mimetype
    if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return "image"
    if ext in {"mp4", "webm", "mov", "mkv", "avi"}:
        return "video"
    if ext in {"mp3", "wav", "ogg", "m4a", "aac", "weba"}:
        return "voice"
    return "file"


def human_readable_size(num_bytes):
    size = float(num_bytes)
    for unit in ["بایت", "کیلوبایت", "مگابایت", "گیگابایت"]:
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "بایت" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} ترابایت"


# ---------------------------------------------------------------------------
# In-memory presence tracking (who's online right now)
# ---------------------------------------------------------------------------
# Maps user_id -> set of active Socket.IO session ids. A user can have the app
# open in more than one tab/device, so we only consider them "offline" once
# every connection has dropped. This is intentionally in-memory (not in the
# database) since it only reflects the current process's live connections.
online_users = defaultdict(set)


def is_user_online(uid):
    return bool(online_users.get(uid))


def user_chat_ids(uid):
    db = get_db()
    rows = db.execute("SELECT chat_id FROM chat_members WHERE user_id=?", (uid,)).fetchall()
    db.close()
    return [r["chat_id"] for r in rows]


def broadcast_presence(uid, online, last_seen=None):
    payload = {"user_id": uid, "online": online, "last_seen": json_time(last_seen)}
    for chat_id in user_chat_ids(uid):
        socketio.emit("presence_update", payload, room=f"chat_{chat_id}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "برای این عملیات باید وارد حساب کاربری خود شوید"}), 401
        return fn(*args, **kwargs)
    return wrapper


def current_user_id():
    return session.get("user_id")


def json_time(value):
    """Return PostgreSQL date/time values in one stable ISO format for API/Socket.IO."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def user_public(row):
    keys = row.keys()
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "avatar_color": row["avatar_color"],
        "avatar_url": row["avatar_url"] if "avatar_url" in keys else None,
        "bio": row["bio"] if "bio" in keys else "",
        "online": is_user_online(row["id"]),
        "last_seen": json_time(row["last_seen"]) if "last_seen" in keys else None,
        "theme_color": row["theme_color"] if "theme_color" in keys else "blue",
        "theme_mode": row["theme_mode"] if "theme_mode" in keys else "dark",
        "notifications_enabled": bool(row["notifications_enabled"]) if "notifications_enabled" in keys else True,
    }


def message_preview_text(message_type, content, file_name):
    if message_type == "image":
        return "🖼️ عکس"
    if message_type == "video":
        return "🎥 ویدیو"
    if message_type == "voice":
        return "🎤 پیام صوتی"
    if message_type == "file":
        return f"📎 فایل: {file_name}" if file_name else "📎 فایل"
    if message_type == "poll":
        return f"📊 نظرسنجی: {content}"
    return content


def archive_is_unlocked():
    # Archive access is session-bound and expires after 15 minutes. The password
    # itself is never stored in the session or returned to the browser.
    unlocked_at = session.get("archive_unlocked_at")
    try:
        return bool(unlocked_at and time.time() - float(unlocked_at) < 15 * 60)
    except (TypeError, ValueError):
        return False


def is_member(db, chat_id, user_id):
    row = db.execute(
        "SELECT * FROM chat_members WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    if row and row.get("archived") and not archive_is_unlocked():
        return None
    return row


def can_post_in_chat(chat_row, membership_row):
    """A channel with posting restricted to admins blocks regular members from
    sending new messages — but this never applies to private chats or groups."""
    if not chat_row or not membership_row:
        return False
    if chat_row["type"] == "channel" and not chat_row["open_chat"]:
        return membership_row["role"] in ("owner", "admin")
    return True


def chat_display_for_user(db, chat, user_id):
    """Build a JSON-friendly dict describing a chat from the point of view of user_id."""
    chat_id = chat["id"]
    chat_keys = chat.keys()
    result = {
        "id": chat_id,
        "type": chat["type"],
        "description": chat["description"],
        "is_public": bool(chat["is_public"]),
        "open_chat": bool(chat["open_chat"]) if "open_chat" in chat_keys else False,
        "owner_id": chat["owner_id"],
    }

    if chat["type"] == "private":
        other = db.execute(
            """SELECT u.* FROM users u
               JOIN chat_members cm ON cm.user_id = u.id
               WHERE cm.chat_id=? AND u.id != ?""",
            (chat_id, user_id),
        ).fetchone()
        result["name"] = other["display_name"] if other else "کاربر حذف شده"
        result["avatar_color"] = other["avatar_color"] if other else "#999999"
        result["avatar_url"] = other["avatar_url"] if other else None
        result["peer_id"] = other["id"] if other else None
        result["online"] = is_user_online(other["id"]) if other else False
        result["last_seen"] = json_time(other["last_seen"]) if other else None
    else:
        result["name"] = chat["name"]
        result["avatar_color"] = "#4e89ff"
        result["avatar_url"] = chat["avatar_url"] if "avatar_url" in chat.keys() else None

    last_msg = db.execute(
        """SELECT m.*, u.display_name as sender_name FROM messages m
           JOIN users u ON u.id = m.sender_id
           WHERE m.chat_id=? ORDER BY m.id DESC LIMIT 1""",
        (chat_id,),
    ).fetchone()
    if last_msg:
        result["last_message"] = {
            "id": last_msg["id"],
            "content": message_preview_text(last_msg["message_type"], last_msg["content"], last_msg["file_name"]),
            "message_type": last_msg["message_type"],
            "created_at": json_time(last_msg["created_at"]),
            "sender_name": last_msg["sender_name"],
            "sender_id": last_msg["sender_id"],
        }
    else:
        result["last_message"] = None

    member_count = db.execute(
        "SELECT COUNT(*) as c FROM chat_members WHERE chat_id=?", (chat_id,)
    ).fetchone()["c"]
    result["member_count"] = member_count

    my_membership = db.execute(
        "SELECT last_read_message_id FROM chat_members WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    my_last_read = my_membership["last_read_message_id"] if my_membership else 0
    unread = db.execute(
        "SELECT COUNT(*) as c FROM messages WHERE chat_id=? AND id > ? AND sender_id != ?",
        (chat_id, my_last_read, user_id),
    ).fetchone()["c"]
    result["unread_count"] = unread

    return result


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()

    if not username or not password or not display_name:
        return jsonify({"error": "نام کاربری، رمز عبور و نام نمایشی الزامی است"}), 400
    if len(password) < 4:
        return jsonify({"error": "رمز عبور باید حداقل ۴ کاراکتر باشد"}), 400

    db = get_db()
    exists = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        db.close()
        return jsonify({"error": "این نام کاربری قبلاً ثبت شده است"}), 400

    color = random.choice(AVATAR_COLORS)
    inserted = db.execute(
        "INSERT INTO users (username, password_hash, display_name, avatar_color) VALUES (?, ?, ?, ?) RETURNING id",
        (username, generate_password_hash(password), display_name, color),
    ).fetchone()
    db.commit()
    user_id = inserted["id"]
    db.close()

    session["user_id"] = user_id
    session["username"] = username
    session.permanent = True  # persist login across browser restarts (~90 days)
    return jsonify({"id": user_id, "username": username, "display_name": display_name, "avatar_color": color})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    db.close()

    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "نام کاربری یا رمز عبور اشتباه است"}), 401

    session["user_id"] = row["id"]
    session["username"] = row["username"]
    session.permanent = True  # persist login across browser restarts (~90 days)
    return jsonify(user_public(row))


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me", methods=["GET"])
def me():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    if not row:
        session.clear()
        return jsonify({"error": "not logged in"}), 401
    return jsonify(user_public(row))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@app.route("/api/users/search", methods=["GET"])
@login_required
def search_users():
    q = (request.args.get("q") or "").strip().lower()
    db = get_db()
    if not q:
        rows = db.execute(
            "SELECT * FROM users WHERE id != ? ORDER BY display_name LIMIT 30",
            (current_user_id(),),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM users WHERE id != ?
               AND (LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?)
               ORDER BY display_name LIMIT 30""",
            (current_user_id(), f"%{q}%", f"%{q}%"),
        ).fetchall()
    db.close()
    return jsonify([user_public(r) for r in rows])


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------

@app.route("/api/chats", methods=["GET"])
@login_required
def list_chats():
    uid = current_user_id()
    db = get_db()
    chats = db.execute(
        """SELECT c.* FROM chats c
           JOIN chat_members cm ON cm.chat_id = c.id
           WHERE cm.user_id = ? AND COALESCE(cm.archived, 0) = 0""",
        (uid,),
    ).fetchall()

    result = [chat_display_for_user(db, c, uid) for c in chats]
    db.close()

    def sort_key(c):
        return c["last_message"]["created_at"] if c["last_message"] else "0"

    result.sort(key=sort_key, reverse=True)
    return jsonify(result)


@app.route("/api/archive/password", methods=["POST"])
@login_required
def set_archive_password():
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    password = data.get("password") or ""
    if len(password) < 4:
        return jsonify({"error": "رمز بایگانی باید حداقل ۴ کاراکتر باشد"}), 400

    db = get_db()
    db.execute("UPDATE users SET archive_password_hash=? WHERE id=?", (generate_password_hash(password), uid))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/archive/verify", methods=["POST"])
@login_required
def verify_archive_password():
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    password = data.get("password") or ""
    db = get_db()
    row = db.execute("SELECT archive_password_hash FROM users WHERE id=?", (uid,)).fetchone()
    db.close()
    if not row or not row["archive_password_hash"]:
        return jsonify({"error": "هنوز رمز بایگانی تنظیم نشده است", "needs_setup": True}), 400
    if not check_password_hash(row["archive_password_hash"], password):
        return jsonify({"error": "رمز بایگانی اشتباه است"}), 403
    session["archive_unlocked_at"] = time.time()
    session.modified = True
    return jsonify({"ok": True})


@app.route("/api/archive/chats", methods=["GET"])
@login_required
def list_archived_chats():
    if not archive_is_unlocked():
        return jsonify({"error": "ابتدا قفل بایگانی را باز کنید"}), 403
    uid = current_user_id()
    db = get_db()
    rows = db.execute(
        """SELECT c.* FROM chats c
           JOIN chat_members cm ON cm.chat_id=c.id
           WHERE cm.user_id=? AND COALESCE(cm.archived, 0)=1""",
        (uid,),
    ).fetchall()
    result = [chat_display_for_user(db, row, uid) for row in rows]
    db.close()
    result.sort(key=lambda c: c["last_message"]["created_at"] if c["last_message"] else "0", reverse=True)
    return jsonify(result)


@app.route("/api/archive/chats/<int:chat_id>", methods=["POST"])
@login_required
def set_chat_archived(chat_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    archived = bool(data.get("archived"))
    db = get_db()
    membership = is_member(db, chat_id, uid)
    if not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    db.execute("UPDATE chat_members SET archived=? WHERE chat_id=? AND user_id=?", (1 if archived else 0, chat_id, uid))
    db.commit()
    db.close()
    return jsonify({"ok": True, "archived": archived})


@app.route("/api/chats/private", methods=["POST"])
@login_required
def create_private_chat():
    data = request.get_json(force=True) or {}
    other_id = data.get("user_id")
    uid = current_user_id()

    if not other_id or int(other_id) == uid:
        return jsonify({"error": "کاربر مقصد نامعتبر است"}), 400

    db = get_db()
    other = db.execute("SELECT * FROM users WHERE id=?", (other_id,)).fetchone()
    if not other:
        db.close()
        return jsonify({"error": "کاربر پیدا نشد"}), 404

    existing = db.execute(
        """SELECT c.id FROM chats c
           JOIN chat_members cm1 ON cm1.chat_id = c.id AND cm1.user_id = ?
           JOIN chat_members cm2 ON cm2.chat_id = c.id AND cm2.user_id = ?
           WHERE c.type = 'private'""",
        (uid, other_id),
    ).fetchone()

    if existing:
        chat_id = existing["id"]
    else:
        row = db.execute(
            "INSERT INTO chats (type, owner_id) VALUES ('private', ?) RETURNING id", (uid,)
        ).fetchone()
        chat_id = row["id"]
        db.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'member')", (chat_id, uid))
        db.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'member')", (chat_id, other_id))
        db.commit()

    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    result = chat_display_for_user(db, chat, uid)
    db.close()
    return jsonify(result)


@app.route("/api/chats/group", methods=["POST"])
@login_required
def create_group():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    member_ids = data.get("member_ids") or []
    uid = current_user_id()

    if not name:
        return jsonify({"error": "نام گروه الزامی است"}), 400

    db = get_db()
    row = db.execute(
        "INSERT INTO chats (type, name, description, owner_id) VALUES ('group', ?, ?, ?) RETURNING id",
        (name, data.get("description", ""), uid),
    ).fetchone()
    chat_id = row["id"]
    db.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'owner')", (chat_id, uid))
    for mid in member_ids:
        if int(mid) != uid:
            db.execute(
                "INSERT OR IGNORE INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'member')",
                (chat_id, mid),
            )
    db.commit()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    result = chat_display_for_user(db, chat, uid)
    db.close()
    return jsonify(result)


@app.route("/api/chats/channel", methods=["POST"])
@login_required
def create_channel():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    member_ids = data.get("member_ids") or []
    is_public = 1 if data.get("is_public") else 0
    open_chat = 1 if data.get("open_chat") else 0
    uid = current_user_id()

    if not name:
        return jsonify({"error": "نام کانال الزامی است"}), 400

    db = get_db()
    row = db.execute(
        "INSERT INTO chats (type, name, description, is_public, open_chat, owner_id) VALUES ('channel', ?, ?, ?, ?, ?) RETURNING id",
        (name, data.get("description", ""), is_public, open_chat, uid),
    ).fetchone()
    chat_id = row["id"]
    db.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'owner')", (chat_id, uid))
    for mid in member_ids:
        if int(mid) != uid:
            db.execute(
                "INSERT OR IGNORE INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'member')",
                (chat_id, mid),
            )
    db.commit()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    result = chat_display_for_user(db, chat, uid)
    db.close()
    return jsonify(result)


@app.route("/api/chats/<int:chat_id>/settings", methods=["POST"])
@login_required
def update_chat_settings(chat_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}

    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] != "channel":
        db.close()
        return jsonify({"error": "این تنظیم فقط برای کانال معنا دارد"}), 400
    if membership["role"] != "owner":
        db.close()
        return jsonify({"error": "فقط مالک کانال می‌تواند این تنظیم را تغییر دهد"}), 403

    if "open_chat" in data:
        db.execute("UPDATE chats SET open_chat=? WHERE id=?", (1 if data["open_chat"] else 0, chat_id))
    db.commit()

    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    result = chat_display_for_user(db, chat, uid)
    db.close()

    socketio.emit(
        "chat_settings_changed",
        {"chat_id": chat_id, "open_chat": result["open_chat"]},
        room=f"chat_{chat_id}",
    )
    return jsonify(result)


@app.route("/api/chats/<int:chat_id>/avatar", methods=["POST"])
@login_required
def update_chat_avatar(chat_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] not in ("group", "channel"):
        db.close()
        return jsonify({"error": "فقط گروه و کانال می‌توانند عکس پروفایل داشته باشند"}), 400
    if membership["role"] not in ("owner", "admin"):
        db.close()
        return jsonify({"error": "فقط مالک یا مدیر می‌تواند عکس گفتگو را تغییر دهد"}), 403

    avatar_file = request.files.get("avatar")
    if not avatar_file or not avatar_file.filename:
        db.close()
        return jsonify({"error": "فایلی ارسال نشده است"}), 400

    original_name = secure_filename(avatar_file.filename)
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "jpg"
    if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
        db.close()
        return jsonify({"error": "فرمت تصویر مجاز نیست"}), 400

    old_avatar = chat["avatar_url"] if "avatar_url" in chat.keys() else None
    stored_name = f"chatavatar_{uuid.uuid4().hex}.{ext}"
    avatar_file.save(os.path.join(UPLOAD_DIR, stored_name))
    new_avatar_url = f"/uploads/{stored_name}"

    db.execute("UPDATE chats SET avatar_url=? WHERE id=?", (new_avatar_url, chat_id))
    db.commit()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    result = chat_display_for_user(db, chat, uid)
    db.close()

    if old_avatar:
        try:
            old_path = os.path.join(UPLOAD_DIR, os.path.basename(old_avatar))
            if os.path.isfile(old_path):
                os.remove(old_path)
        except OSError:
            pass

    socketio.emit("chat_avatar_changed", {"chat_id": chat_id, "avatar_url": new_avatar_url}, room=f"chat_{chat_id}")
    return jsonify(result)


@app.route("/api/chats/<int:chat_id>/avatar", methods=["DELETE"])
@login_required
def remove_chat_avatar(chat_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if membership["role"] not in ("owner", "admin"):
        db.close()
        return jsonify({"error": "فقط مالک یا مدیر می‌تواند عکس گفتگو را حذف کند"}), 403

    old_avatar = chat["avatar_url"] if "avatar_url" in chat.keys() else None
    db.execute("UPDATE chats SET avatar_url=NULL WHERE id=?", (chat_id,))
    db.commit()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    result = chat_display_for_user(db, chat, uid)
    db.close()

    if old_avatar:
        try:
            old_path = os.path.join(UPLOAD_DIR, os.path.basename(old_avatar))
            if os.path.isfile(old_path):
                os.remove(old_path)
        except OSError:
            pass

    socketio.emit("chat_avatar_changed", {"chat_id": chat_id, "avatar_url": None}, room=f"chat_{chat_id}")
    return jsonify(result)


@app.route("/api/chats/<int:chat_id>", methods=["GET"])
@login_required
def get_chat(chat_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat or not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی به این گفتگو ندارید"}), 403
    result = chat_display_for_user(db, chat, uid)
    db.close()
    return jsonify(result)


@app.route("/api/chats/<int:chat_id>/members", methods=["GET"])
@login_required
def get_members(chat_id):
    uid = current_user_id()
    db = get_db()
    if not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    rows = db.execute(
        """SELECT u.*, cm.role FROM users u
           JOIN chat_members cm ON cm.user_id = u.id
           WHERE cm.chat_id=? ORDER BY cm.role, u.display_name""",
        (chat_id,),
    ).fetchall()
    db.close()
    return jsonify([{**user_public(r), "role": r["role"]} for r in rows])


@app.route("/api/chats/<int:chat_id>/members", methods=["POST"])
@login_required
def add_members(chat_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    member_ids = data.get("member_ids") or []

    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)

    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] == "private":
        db.close()
        return jsonify({"error": "افزودن عضو به گفتگوی خصوصی ممکن نیست"}), 400
    if membership["role"] not in ("owner", "admin"):
        db.close()
        return jsonify({"error": "فقط مالک یا مدیر می‌تواند عضو اضافه کند"}), 403

    added = []
    for mid in member_ids:
        db.execute(
            "INSERT OR IGNORE INTO chat_members (chat_id, user_id, role) VALUES (?, ?, 'member')",
            (chat_id, mid),
        )
        added.append(mid)
    db.commit()
    db.close()

    socketio.emit("members_added", {"chat_id": chat_id, "member_ids": added}, room=f"chat_{chat_id}")
    return jsonify({"ok": True, "added": added})


@app.route("/api/chats/<int:chat_id>/members/<int:member_id>/role", methods=["POST"])
@login_required
def change_member_role(chat_id, member_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    new_role = data.get("role")
    if new_role not in ("admin", "member"):
        return jsonify({"error": "نقش نامعتبر است"}), 400

    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] == "private":
        db.close()
        return jsonify({"error": "در گفتگوی خصوصی نقشی وجود ندارد"}), 400
    if membership["role"] != "owner":
        db.close()
        return jsonify({"error": "فقط مالک می‌تواند نقش اعضا را تغییر دهد"}), 403

    target = db.execute(
        "SELECT * FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, member_id)
    ).fetchone()
    if not target:
        db.close()
        return jsonify({"error": "عضو پیدا نشد"}), 404
    if target["role"] == "owner":
        db.close()
        return jsonify({"error": "نقش مالک قابل تغییر نیست"}), 400

    db.execute(
        "UPDATE chat_members SET role=? WHERE chat_id=? AND user_id=?", (new_role, chat_id, member_id)
    )
    db.commit()
    db.close()

    socketio.emit(
        "member_role_changed", {"chat_id": chat_id, "user_id": member_id, "role": new_role}, room=f"chat_{chat_id}"
    )
    return jsonify({"ok": True, "user_id": member_id, "role": new_role})


@app.route("/api/chats/<int:chat_id>/members/<int:member_id>", methods=["DELETE"])
@login_required
def remove_member(chat_id, member_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] == "private":
        db.close()
        return jsonify({"error": "امکان حذف عضو در گفتگوی خصوصی وجود ندارد"}), 400

    target = db.execute(
        "SELECT * FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, member_id)
    ).fetchone()
    if not target:
        db.close()
        return jsonify({"error": "عضو پیدا نشد"}), 404
    if target["role"] == "owner":
        db.close()
        return jsonify({"error": "امکان حذف مالک گفتگو وجود ندارد"}), 400
    if membership["role"] not in ("owner", "admin"):
        db.close()
        return jsonify({"error": "فقط مالک یا مدیر می‌تواند عضو را حذف کند"}), 403
    if membership["role"] == "admin" and target["role"] == "admin":
        db.close()
        return jsonify({"error": "یک مدیر نمی‌تواند مدیر دیگری را حذف کند"}), 403

    db.execute("DELETE FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, member_id))
    db.commit()
    db.close()

    socketio.emit("member_removed", {"chat_id": chat_id, "user_id": member_id, "reason": "kicked"}, room=f"chat_{chat_id}")
    socketio.emit("removed_from_chat", {"chat_id": chat_id, "reason": "kicked"}, room=f"user_{member_id}")
    return jsonify({"ok": True})


@app.route("/api/chats/<int:chat_id>/leave", methods=["POST"])
@login_required
def leave_chat(chat_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] == "private":
        db.close()
        return jsonify({"error": "گفتگوی خصوصی قابل ترک کردن نیست"}), 400
    if membership["role"] == "owner":
        db.close()
        return jsonify({"error": "مالک نمی‌تواند گفتگو را ترک کند؛ می‌توانید آن را حذف کنید"}), 400

    db.execute("DELETE FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, uid))
    db.commit()
    db.close()

    socketio.emit("member_removed", {"chat_id": chat_id, "user_id": uid, "reason": "left"}, room=f"chat_{chat_id}")
    socketio.emit("removed_from_chat", {"chat_id": chat_id, "reason": "left"}, room=f"user_{uid}")
    return jsonify({"ok": True})


@app.route("/api/chats/<int:chat_id>", methods=["DELETE"])
@login_required
def delete_chat(chat_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] == "private":
        db.close()
        return jsonify({"error": "گفتگوی خصوصی قابل حذف نیست"}), 400
    if membership["role"] != "owner":
        db.close()
        return jsonify({"error": "فقط مالک می‌تواند این گفتگو را حذف کند"}), 403

    file_rows = db.execute(
        "SELECT file_url FROM messages WHERE chat_id=? AND file_url IS NOT NULL", (chat_id,)
    ).fetchall()
    member_rows = db.execute("SELECT user_id FROM chat_members WHERE chat_id=?", (chat_id,)).fetchall()
    member_ids = [r["user_id"] for r in member_rows]

    db.execute("DELETE FROM chats WHERE id=?", (chat_id,))  # cascades to chat_members & messages
    db.commit()
    db.close()

    for row in file_rows:
        try:
            path = os.path.join(UPLOAD_DIR, os.path.basename(row["file_url"]))
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    for member_uid in member_ids:
        socketio.emit("chat_deleted", {"chat_id": chat_id}, room=f"user_{member_uid}")

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@app.route("/api/chats/<int:chat_id>/messages", methods=["GET"])
@login_required
def get_messages(chat_id):
    uid = current_user_id()
    db = get_db()
    if not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403

    before_id = request.args.get("before_id", type=int)
    if before_id:
        rows = db.execute(
            """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
               FROM messages m JOIN users u ON u.id = m.sender_id
               WHERE m.chat_id=? AND m.id < ? ORDER BY m.id DESC LIMIT 50""",
            (chat_id, before_id),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
               FROM messages m JOIN users u ON u.id = m.sender_id
               WHERE m.chat_id=? ORDER BY m.id DESC LIMIT 50""",
            (chat_id,),
        ).fetchall()

    messages = [serialize_message(db, r, viewer_id=uid) for r in rows]
    db.close()
    messages.reverse()
    return jsonify(messages)


def fetch_reply_preview(db, reply_to_id):
    if not reply_to_id:
        return None
    row = db.execute(
        """SELECT m.id, m.message_type, m.content, m.file_name, u.display_name as sender_name
           FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (reply_to_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "sender_name": row["sender_name"],
        "preview": message_preview_text(row["message_type"], row["content"], row["file_name"]),
    }


def get_reactions_summary(db, message_id):
    """Returns e.g. [{"emoji": "👍", "count": 2, "user_ids": [1, 4]}, ...], sorted by count desc.
    No "reacted by me" flag here on purpose: this exact payload is broadcast as-is to every
    member of the chat, and each of their clients can just check if their own id is in user_ids."""
    rows = db.execute(
        "SELECT emoji, user_id FROM message_reactions WHERE message_id=? ORDER BY id",
        (message_id,),
    ).fetchall()
    summary = {}
    for row in rows:
        entry = summary.setdefault(row["emoji"], {"emoji": row["emoji"], "count": 0, "user_ids": []})
        entry["count"] += 1
        entry["user_ids"].append(row["user_id"])
    return sorted(summary.values(), key=lambda x: -x["count"])


def serialize_poll(db, poll_id, viewer_id=None):
    """`is_correct` is always included for every option (it's static data, not a
    secret) — the frontend is what decides not to reveal it visually until the
    viewer has answered. `my_option_id` is per-viewer and therefore only meaningful
    for a direct REST response to that viewer; socket broadcasts always pass
    viewer_id=None here so they never leak one person's vote into everyone's feed."""
    poll = db.execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()
    if not poll:
        return None

    options = db.execute(
        "SELECT * FROM poll_options WHERE poll_id=? ORDER BY option_order", (poll_id,)
    ).fetchall()
    total_votes = db.execute(
        "SELECT COUNT(*) as c FROM poll_votes WHERE poll_id=?", (poll_id,)
    ).fetchone()["c"]

    my_option_id = None
    if viewer_id:
        mine = db.execute(
            "SELECT option_id FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, viewer_id)
        ).fetchone()
        my_option_id = mine["option_id"] if mine else None

    option_list = []
    for opt in options:
        votes = db.execute(
            "SELECT COUNT(*) as c FROM poll_votes WHERE poll_id=? AND option_id=?",
            (poll_id, opt["id"]),
        ).fetchone()["c"]
        percent = round((votes / total_votes) * 100) if total_votes else 0
        option_list.append({
            "id": opt["id"],
            "text": opt["option_text"],
            "votes": votes,
            "percent": percent,
            "is_correct": bool(poll["poll_type"] == "quiz" and poll["correct_option_id"] == opt["id"]),
        })

    return {
        "id": poll["id"],
        "message_id": poll["message_id"],
        "chat_id": poll["chat_id"],
        "creator_id": poll["creator_id"],
        "question": poll["question"],
        "poll_type": poll["poll_type"],
        "total_votes": total_votes,
        "my_option_id": my_option_id,
        "options": option_list,
    }


def serialize_message(db, r, viewer_id=None):
    poll_data = None
    if r["message_type"] == "poll":
        poll_row = db.execute("SELECT id FROM polls WHERE message_id=?", (r["id"],)).fetchone()
        if poll_row:
            poll_data = serialize_poll(db, poll_row["id"], viewer_id)

    return {
        "id": r["id"],
        "chat_id": r["chat_id"],
        "sender_id": r["sender_id"],
        "sender_name": r["sender_name"],
        "sender_color": r["sender_color"],
        "sender_avatar": r["sender_avatar"],
        "content": r["content"],
        "message_type": r["message_type"],
        "file_url": r["file_url"],
        "file_name": r["file_name"],
        "file_size": r["file_size"],
        "created_at": json_time(r["created_at"]),
        "edited_at": json_time(r["edited_at"]) if "edited_at" in r.keys() else None,
        "forwarded_from_name": r["forwarded_from_name"] if "forwarded_from_name" in r.keys() else None,
        "reply_to": fetch_reply_preview(db, r["reply_to_id"] if "reply_to_id" in r.keys() else None),
        "reactions": get_reactions_summary(db, r["id"]),
        "poll": poll_data,
    }


@app.route("/api/messages/<int:message_id>", methods=["PUT"])
@login_required
def edit_message(message_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    new_content = (data.get("content") or "").strip()
    if not new_content:
        return jsonify({"error": "متن پیام نمی‌تواند خالی باشد"}), 400

    db = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    if not msg:
        db.close()
        return jsonify({"error": "پیام پیدا نشد"}), 404
    if msg["sender_id"] != uid:
        db.close()
        return jsonify({"error": "فقط فرستنده می‌تواند پیام را ویرایش کند"}), 403
    if msg["message_type"] != "text":
        db.close()
        return jsonify({"error": "فقط پیام‌های متنی قابل ویرایش هستند"}), 400

    db.execute(
        "UPDATE messages SET content=?, edited_at=datetime('now') WHERE id=?",
        (new_content, message_id),
    )
    db.commit()
    row = db.execute(
        """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
           FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (message_id,),
    ).fetchone()
    payload = serialize_message(db, row)
    db.close()

    socketio.emit("message_edited", payload, room=f"chat_{payload['chat_id']}")
    return jsonify(payload)


@app.route("/api/messages/<int:message_id>", methods=["DELETE"])
@login_required
def delete_message(message_id):
    uid = current_user_id()
    db = get_db()

    msg = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    if not msg:
        db.close()
        return jsonify({"error": "پیام پیدا نشد"}), 404

    chat_id = msg["chat_id"]
    membership = is_member(db, chat_id, uid)
    if not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403

    is_owner_of_message = msg["sender_id"] == uid
    is_chat_manager = membership["role"] in ("owner", "admin")
    if not (is_owner_of_message or is_chat_manager):
        db.close()
        return jsonify({"error": "فقط فرستنده پیام یا مدیر گفتگو می‌تواند آن را حذف کند"}), 403

    file_url = msg["file_url"]
    db.execute("DELETE FROM messages WHERE id=?", (message_id,))
    db.commit()
    db.close()

    # Physically remove the uploaded image/video/voice/file from disk too
    if file_url:
        try:
            filename = os.path.basename(file_url)
            file_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            pass

    socketio.emit("message_deleted", {"chat_id": chat_id, "message_id": message_id}, room=f"chat_{chat_id}")
    return jsonify({"ok": True, "message_id": message_id, "chat_id": chat_id})


# ---------------------------------------------------------------------------
# Forward a message to one or more chats (and/or to Saved Messages)
# ---------------------------------------------------------------------------

@app.route("/api/messages/<int:message_id>/forward", methods=["POST"])
@login_required
def forward_message(message_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    target_chat_ids = data.get("chat_ids") or []
    to_saved = bool(data.get("to_saved"))

    if not target_chat_ids and not to_saved:
        return jsonify({"error": "حداقل یک مقصد برای فوروارد انتخاب کنید"}), 400

    db = get_db()
    original = db.execute(
        """SELECT m.*, u.display_name as sender_name FROM messages m
           JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (message_id,),
    ).fetchone()
    if not original:
        db.close()
        return jsonify({"error": "پیام پیدا نشد"}), 404
    if not is_member(db, original["chat_id"], uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if original["message_type"] == "poll":
        db.close()
        return jsonify({"error": "امکان فوروارد نظرسنجی وجود ندارد"}), 400

    # If this message was itself already a forward, keep pointing at the TRUE
    # original author rather than chaining "forwarded from a forward of...".
    origin_name = original["forwarded_from_name"] or original["sender_name"]

    forwarded_to_chats = 0
    skipped_chats = 0
    saved_ok = False

    for chat_id in target_chat_ids:
        membership = is_member(db, chat_id, uid)
        chat = db.execute("SELECT type, open_chat FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not can_post_in_chat(chat, membership):
            skipped_chats += 1
            continue

        inserted = db.execute(
            """INSERT INTO messages (chat_id, sender_id, content, message_type, file_url, file_name, file_size, forwarded_from_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (chat_id, uid, original["content"], original["message_type"],
             original["file_url"], original["file_name"], original["file_size"], origin_name),
        ).fetchone()
        db.commit()
        new_row = db.execute(
            """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
               FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
            (inserted["id"],),
        ).fetchone()
        payload = serialize_message(db, new_row)
        socketio.emit("new_message", payload, room=f"chat_{chat_id}")
        forwarded_to_chats += 1

    if to_saved:
        db.execute(
            """INSERT INTO saved_items (user_id, content, message_type, file_url, file_name, file_size, forwarded_from_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uid, original["content"], original["message_type"],
             original["file_url"], original["file_name"], original["file_size"], origin_name),
        )
        db.commit()
        saved_ok = True

    db.close()
    return jsonify({"ok": True, "forwarded_to_chats": forwarded_to_chats, "skipped_chats": skipped_chats, "saved": saved_ok})


# ---------------------------------------------------------------------------
# Pinned messages (groups & channels only)
# ---------------------------------------------------------------------------

def serialize_pin(db, pin_row):
    msg = db.execute(
        """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
           FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (pin_row["message_id"],),
    ).fetchone()
    if not msg:
        return None
    return {
        "message_id": pin_row["message_id"],
        "pinned_by": pin_row["pinned_by"],
        "pinned_at": json_time(pin_row["pinned_at"]),
        "sender_name": msg["sender_name"],
        "message_type": msg["message_type"],
        "preview": message_preview_text(msg["message_type"], msg["content"], msg["file_name"]),
    }


@app.route("/api/chats/<int:chat_id>/pins", methods=["GET"])
@login_required
def list_pins(chat_id):
    uid = current_user_id()
    db = get_db()
    if not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    rows = db.execute(
        "SELECT * FROM chat_pins WHERE chat_id=? ORDER BY pinned_at DESC", (chat_id,)
    ).fetchall()
    pins = [p for p in (serialize_pin(db, r) for r in rows) if p]
    db.close()
    return jsonify(pins)


@app.route("/api/chats/<int:chat_id>/pins", methods=["POST"])
@login_required
def pin_message(chat_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    message_id = data.get("message_id")

    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] not in ("group", "channel"):
        db.close()
        return jsonify({"error": "پین کردن فقط در گروه و کانال ممکن است"}), 400
    if membership["role"] not in ("owner", "admin"):
        db.close()
        return jsonify({"error": "فقط مالک یا مدیر می‌تواند پیام را پین کند"}), 403

    msg = db.execute("SELECT id FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)).fetchone()
    if not msg:
        db.close()
        return jsonify({"error": "پیام پیدا نشد"}), 404

    db.execute(
        "INSERT OR IGNORE INTO chat_pins (chat_id, message_id, pinned_by) VALUES (?, ?, ?)",
        (chat_id, message_id, uid),
    )
    db.commit()

    pin_row = db.execute(
        "SELECT * FROM chat_pins WHERE chat_id=? AND message_id=?", (chat_id, message_id)
    ).fetchone()
    pin = serialize_pin(db, pin_row)
    db.close()

    socketio.emit("message_pinned", {"chat_id": chat_id, "pin": pin}, room=f"chat_{chat_id}")
    return jsonify(pin)


@app.route("/api/chats/<int:chat_id>/pins/<int:message_id>", methods=["DELETE"])
@login_required
def unpin_message(chat_id, message_id):
    uid = current_user_id()
    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if membership["role"] not in ("owner", "admin"):
        db.close()
        return jsonify({"error": "فقط مالک یا مدیر می‌تواند پیام را از حالت پین خارج کند"}), 403

    db.execute("DELETE FROM chat_pins WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    db.commit()
    db.close()

    socketio.emit("message_unpinned", {"chat_id": chat_id, "message_id": message_id}, room=f"chat_{chat_id}")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Saved Messages (personal notes-to-self — text, images, files, voice; only
# ever visible to their owner, never synced to anyone else)
# ---------------------------------------------------------------------------

def serialize_saved_item(r):
    return {
        "id": r["id"],
        "content": r["content"],
        "message_type": r["message_type"],
        "file_url": r["file_url"],
        "file_name": r["file_name"],
        "file_size": r["file_size"],
        "forwarded_from_name": r["forwarded_from_name"],
        "edited_at": json_time(r["edited_at"]),
        "created_at": json_time(r["created_at"]),
    }


@app.route("/api/saved", methods=["GET"])
@login_required
def list_saved_items():
    uid = current_user_id()
    db = get_db()
    rows = db.execute("SELECT * FROM saved_items WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    db.close()
    return jsonify([serialize_saved_item(r) for r in rows])


@app.route("/api/saved", methods=["POST"])
@login_required
def create_saved_item():
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    message_type = data.get("message_type") or "text"
    file_url = data.get("file_url")
    file_name = data.get("file_name")
    file_size = data.get("file_size")

    if message_type == "text" and not content:
        return jsonify({"error": "متن یادداشت نمی‌تواند خالی باشد"}), 400
    if message_type != "text" and not file_url:
        return jsonify({"error": "فایل ارسال نشده است"}), 400

    db = get_db()
    inserted = db.execute(
        """INSERT INTO saved_items (user_id, content, message_type, file_url, file_name, file_size)
           VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
        (uid, content, message_type, file_url, file_name, file_size),
    ).fetchone()
    db.commit()
    row = db.execute("SELECT * FROM saved_items WHERE id=?", (inserted["id"],)).fetchone()
    db.close()
    return jsonify(serialize_saved_item(row))


@app.route("/api/saved/<int:item_id>", methods=["PUT"])
@login_required
def edit_saved_item(item_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    new_content = (data.get("content") or "").strip()
    if not new_content:
        return jsonify({"error": "متن یادداشت نمی‌تواند خالی باشد"}), 400

    db = get_db()
    row = db.execute("SELECT * FROM saved_items WHERE id=? AND user_id=?", (item_id, uid)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "یادداشت پیدا نشد"}), 404
    if row["message_type"] != "text":
        db.close()
        return jsonify({"error": "فقط یادداشت‌های متنی قابل ویرایش هستند"}), 400

    db.execute(
        "UPDATE saved_items SET content=?, edited_at=datetime('now') WHERE id=?",
        (new_content, item_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM saved_items WHERE id=?", (item_id,)).fetchone()
    db.close()
    return jsonify(serialize_saved_item(row))


@app.route("/api/saved/<int:item_id>", methods=["DELETE"])
@login_required
def delete_saved_item(item_id):
    uid = current_user_id()
    db = get_db()
    row = db.execute("SELECT * FROM saved_items WHERE id=? AND user_id=?", (item_id, uid)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "یادداشت پیدا نشد"}), 404

    file_url = row["file_url"]
    db.execute("DELETE FROM saved_items WHERE id=?", (item_id,))
    db.commit()
    db.close()

    if file_url:
        try:
            path = os.path.join(UPLOAD_DIR, os.path.basename(file_url))
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Polls & quizzes (groups and channels only)
# ---------------------------------------------------------------------------

@app.route("/api/chats/<int:chat_id>/polls", methods=["POST"])
@login_required
def create_poll(chat_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    raw_options = data.get("options") or []
    poll_type = data.get("poll_type") or "regular"
    correct_index = data.get("correct_option_index")

    if poll_type not in ("regular", "quiz"):
        return jsonify({"error": "نوع نظرسنجی نامعتبر است"}), 400
    if not question:
        return jsonify({"error": "متن سوال الزامی است"}), 400

    options = [o.strip() for o in raw_options if o and o.strip()]
    if len(options) < 2:
        return jsonify({"error": "نظرسنجی باید حداقل ۲ گزینه داشته باشد"}), 400
    if len(options) > 10:
        return jsonify({"error": "نظرسنجی حداکثر می‌تواند ۱۰ گزینه داشته باشد"}), 400

    if poll_type == "quiz":
        if not isinstance(correct_index, int) or not (0 <= correct_index < len(options)):
            return jsonify({"error": "برای آزمون باید یک گزینه به‌عنوان جواب درست انتخاب کنید"}), 400

    db = get_db()
    chat = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    membership = is_member(db, chat_id, uid)
    if not chat or not membership:
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    if chat["type"] not in ("group", "channel"):
        db.close()
        return jsonify({"error": "نظرسنجی فقط در گروه و کانال قابل ساخت است"}), 400
    if not can_post_in_chat(chat, membership):
        db.close()
        return jsonify({"error": "شما اجازه ارسال پیام در این کانال را ندارید"}), 403

    message_row = db.execute(
        "INSERT INTO messages (chat_id, sender_id, content, message_type) VALUES (?, ?, ?, 'poll') RETURNING id",
        (chat_id, uid, question),
    ).fetchone()
    message_id = message_row["id"]

    poll_row = db.execute(
        "INSERT INTO polls (message_id, chat_id, creator_id, question, poll_type) VALUES (?, ?, ?, ?, ?) RETURNING id",
        (message_id, chat_id, uid, question, poll_type),
    ).fetchone()
    poll_id = poll_row["id"]

    option_ids = []
    for idx, text in enumerate(options):
        option_row = db.execute(
            "INSERT INTO poll_options (poll_id, option_text, option_order) VALUES (?, ?, ?) RETURNING id",
            (poll_id, text, idx),
        ).fetchone()
        option_ids.append(option_row["id"])

    if poll_type == "quiz":
        db.execute(
            "UPDATE polls SET correct_option_id=? WHERE id=?",
            (option_ids[correct_index], poll_id),
        )

    db.commit()

    row = db.execute(
        """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
           FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (message_id,),
    ).fetchone()
    # viewer_id=None: this exact payload is broadcast to the whole chat, and a
    # brand-new poll has zero votes yet anyway, so "my_option_id" is null for everyone.
    payload = serialize_message(db, row, viewer_id=None)
    db.close()

    socketio.emit("new_message", payload, room=f"chat_{chat_id}")
    return jsonify(payload)


@app.route("/api/polls/<int:poll_id>/vote", methods=["POST"])
@login_required
def vote_poll(poll_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    option_id = data.get("option_id")

    db = get_db()
    poll = db.execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()
    if not poll:
        db.close()
        return jsonify({"error": "نظرسنجی پیدا نشد"}), 404
    if not is_member(db, poll["chat_id"], uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403

    option = db.execute(
        "SELECT * FROM poll_options WHERE id=? AND poll_id=?", (option_id, poll_id)
    ).fetchone()
    if not option:
        db.close()
        return jsonify({"error": "گزینه نامعتبر است"}), 400

    existing = db.execute(
        "SELECT * FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, uid)
    ).fetchone()

    if existing:
        if poll["poll_type"] == "quiz":
            db.close()
            return jsonify({"error": "شما قبلاً به این آزمون پاسخ داده‌اید"}), 400
        db.execute(
            "UPDATE poll_votes SET option_id=?, voted_at=datetime('now') WHERE id=?",
            (option_id, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO poll_votes (poll_id, option_id, user_id) VALUES (?, ?, ?)",
            (poll_id, option_id, uid),
        )
    db.commit()

    # Broadcast the objective (non-personalized) vote counts to everyone in the chat...
    objective = serialize_poll(db, poll_id, viewer_id=None)
    socketio.emit("poll_voted", {"chat_id": poll["chat_id"], "poll": objective}, room=f"chat_{poll['chat_id']}")

    # ...but hand the voter back their own personalized view (their pick + correctness).
    personalized = serialize_poll(db, poll_id, viewer_id=uid)
    db.close()
    return jsonify(personalized)


# ---------------------------------------------------------------------------
# Reactions (like Telegram/WhatsApp: one reaction per person per message —
# picking the same emoji again removes it, picking a different one swaps it)
# ---------------------------------------------------------------------------

@app.route("/api/messages/<int:message_id>/react", methods=["POST"])
@login_required
def react_to_message(message_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    emoji = data.get("emoji")
    if emoji not in ALLOWED_REACTIONS:
        return jsonify({"error": "این ری‌اکشن مجاز نیست"}), 400

    db = get_db()
    msg = db.execute("SELECT chat_id FROM messages WHERE id=?", (message_id,)).fetchone()
    if not msg:
        db.close()
        return jsonify({"error": "پیام پیدا نشد"}), 404
    chat_id = msg["chat_id"]

    # Reacting is allowed for every member — even read-only channel viewers who
    # can't post messages — so this intentionally does NOT check owner/admin/open_chat.
    if not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403

    existing = db.execute(
        "SELECT * FROM message_reactions WHERE message_id=? AND user_id=?", (message_id, uid)
    ).fetchone()

    if existing and existing["emoji"] == emoji:
        db.execute("DELETE FROM message_reactions WHERE id=?", (existing["id"],))
    elif existing:
        db.execute(
            "UPDATE message_reactions SET emoji=?, created_at=datetime('now') WHERE id=?",
            (emoji, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO message_reactions (message_id, user_id, emoji) VALUES (?, ?, ?)",
            (message_id, uid, emoji),
        )
    db.commit()

    reactions = get_reactions_summary(db, message_id)
    db.close()

    socketio.emit(
        "reaction_updated",
        {"chat_id": chat_id, "message_id": message_id, "reactions": reactions},
        room=f"chat_{chat_id}",
    )
    return jsonify({"ok": True, "reactions": reactions})


# ---------------------------------------------------------------------------
# Read receipts ("seen" ticks)
# ---------------------------------------------------------------------------

@app.route("/api/chats/<int:chat_id>/read", methods=["POST"])
@login_required
def mark_chat_read(chat_id):
    uid = current_user_id()
    db = get_db()
    if not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403

    latest = db.execute("SELECT MAX(id) as m FROM messages WHERE chat_id=?", (chat_id,)).fetchone()
    latest_id = latest["m"] or 0

    db.execute(
        """UPDATE chat_members SET last_read_message_id=?
           WHERE chat_id=? AND user_id=? AND last_read_message_id < ?""",
        (latest_id, chat_id, uid, latest_id),
    )
    db.commit()
    db.close()

    socketio.emit(
        "messages_read",
        {"chat_id": chat_id, "user_id": uid, "last_read_message_id": latest_id},
        room=f"chat_{chat_id}",
    )
    return jsonify({"ok": True, "last_read_message_id": latest_id})


@app.route("/api/chats/<int:chat_id>/read-state", methods=["GET"])
@login_required
def get_read_state(chat_id):
    uid = current_user_id()
    db = get_db()
    if not is_member(db, chat_id, uid):
        db.close()
        return jsonify({"error": "دسترسی ندارید"}), 403
    rows = db.execute(
        "SELECT user_id, last_read_message_id FROM chat_members WHERE chat_id=?",
        (chat_id,),
    ).fetchall()
    db.close()
    return jsonify([{"user_id": r["user_id"], "last_read_message_id": r["last_read_message_id"]} for r in rows])


# ---------------------------------------------------------------------------
# File / media upload (images, videos, voice notes, generic files)
# ---------------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "فایلی ارسال نشده است"}), 400

    original_name = secure_filename(file.filename) or "file"
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""

    if ext and ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "این نوع فایل مجاز نیست"}), 400

    stored_name = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, stored_name)
    file.save(path)

    size = os.path.getsize(path)
    if size == 0:
        os.remove(path)
        return jsonify({"error": "فایل خالی است"}), 400

    mimetype = file.mimetype or mimetypes.guess_type(path)[0]
    is_voice_flag = request.form.get("is_voice") == "1"
    msg_type = "voice" if is_voice_flag else guess_message_type(mimetype, ext)

    return jsonify({
        "file_url": f"/uploads/{stored_name}",
        "file_name": original_name,
        "file_size": size,
        "file_size_readable": human_readable_size(size),
        "message_type": msg_type,
    })


@app.route("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({"error": "حجم فایل بیشتر از حد مجاز (۶۰ مگابایت) است"}), 413


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@app.route("/api/profile", methods=["POST"])
@login_required
def update_profile():
    uid = current_user_id()
    db = get_db()

    display_name = (request.form.get("display_name") or "").strip()
    bio = request.form.get("bio")
    theme_color = request.form.get("theme_color")
    theme_mode = request.form.get("theme_mode")
    notifications_enabled = request.form.get("notifications_enabled")
    avatar_file = request.files.get("avatar")

    updates = []
    params = []

    if display_name:
        updates.append("display_name=?")
        params.append(display_name)

    if bio is not None:
        updates.append("bio=?")
        params.append(bio.strip())

    if theme_color is not None:
        if theme_color not in THEME_COLOR_PRESETS:
            db.close()
            return jsonify({"error": "رنگ تم نامعتبر است"}), 400
        updates.append("theme_color=?")
        params.append(theme_color)

    if theme_mode is not None:
        if theme_mode not in THEME_MODES:
            db.close()
            return jsonify({"error": "حالت نمایش نامعتبر است"}), 400
        updates.append("theme_mode=?")
        params.append(theme_mode)

    if notifications_enabled is not None:
        updates.append("notifications_enabled=?")
        params.append(1 if notifications_enabled == "1" else 0)

    if avatar_file and avatar_file.filename:
        original_name = secure_filename(avatar_file.filename)
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "jpg"
        if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
            db.close()
            return jsonify({"error": "فرمت تصویر پروفایل مجاز نیست"}), 400
        stored_name = f"avatar_{uuid.uuid4().hex}.{ext}"
        avatar_file.save(os.path.join(UPLOAD_DIR, stored_name))
        updates.append("avatar_url=?")
        params.append(f"/uploads/{stored_name}")

    if updates:
        params.append(uid)
        db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
        db.commit()

    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    db.close()

    updated = user_public(row)
    socketio.emit("profile_updated", updated, room=f"user_{uid}")
    return jsonify(updated)


@app.route("/api/profile/avatar", methods=["DELETE"])
@login_required
def remove_avatar():
    uid = current_user_id()
    db = get_db()
    db.execute("UPDATE users SET avatar_url=NULL WHERE id=?", (uid,))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    db.close()
    return jsonify(user_public(row))


@app.route("/api/profile/username", methods=["POST"])
@login_required
def change_username():
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    new_username = (data.get("username") or "").strip().lower()

    if not new_username or len(new_username) < 3:
        return jsonify({"error": "نام کاربری باید حداقل ۳ کاراکتر باشد"}), 400
    if not re.match(r"^[a-z0-9_]+$", new_username):
        return jsonify({"error": "نام کاربری فقط می‌تواند شامل حروف انگلیسی، عدد و _ باشد"}), 400

    db = get_db()
    exists = db.execute(
        "SELECT id FROM users WHERE username=? AND id != ?", (new_username, uid)
    ).fetchone()
    if exists:
        db.close()
        return jsonify({"error": "این نام کاربری قبلاً گرفته شده است"}), 400

    db.execute("UPDATE users SET username=? WHERE id=?", (new_username, uid))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    db.close()

    session["username"] = new_username
    return jsonify(user_public(row))


@app.route("/api/profile/password", methods=["POST"])
@login_required
def change_password():
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""

    if len(new_password) < 4:
        return jsonify({"error": "رمز عبور جدید باید حداقل ۴ کاراکتر باشد"}), 400

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], current_password):
        db.close()
        return jsonify({"error": "رمز عبور فعلی اشتباه است"}), 400

    db.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(new_password), uid),
    )
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


# ---------------------------------------------------------------------------
# Socket.IO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    uid = session.get("user_id")
    if not uid:
        return False
    db = get_db()
    rows = db.execute("SELECT chat_id FROM chat_members WHERE user_id=?", (uid,)).fetchall()
    db.close()
    for r in rows:
        join_room(f"chat_{r['chat_id']}")
    join_room(f"user_{uid}")

    was_offline = not is_user_online(uid)
    online_users[uid].add(request.sid)
    if was_offline:
        broadcast_presence(uid, True, None)


@socketio.on("disconnect")
def on_disconnect():
    uid = session.get("user_id")
    if not uid:
        return
    online_users[uid].discard(request.sid)
    if not online_users[uid]:
        online_users.pop(uid, None)
        db = get_db()
        db.execute("UPDATE users SET last_seen=datetime('now') WHERE id=?", (uid,))
        db.commit()
        row = db.execute("SELECT last_seen FROM users WHERE id=?", (uid,)).fetchone()
        db.close()
        broadcast_presence(uid, False, row["last_seen"] if row else None)


@app.route("/api/chats/<int:chat_id>/messages", methods=["POST"])
@login_required
def send_message_rest(chat_id):
    uid = current_user_id()
    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    message_type = data.get("message_type") or "text"
    reply_to_id = data.get("reply_to_id")

    if message_type != "text":
        return jsonify({"error": "ارسال این نوع پیام از این مسیر پشتیبانی نمی‌شود"}), 400
    if not content:
        return jsonify({"error": "متن پیام خالی است"}), 400

    db = get_db()
    membership = is_member(db, chat_id, uid)
    if not membership:
        db.close()
        return jsonify({"error": "دسترسی به این گفتگو ندارید"}), 403

    chat = db.execute("SELECT type, open_chat FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not can_post_in_chat(chat, membership):
        db.close()
        return jsonify({"error": "فقط مالک و مدیران این کانال می‌توانند پیام ارسال کنند"}), 403

    if reply_to_id:
        reply_check = db.execute(
            "SELECT id FROM messages WHERE id=? AND chat_id=?", (reply_to_id, chat_id)
        ).fetchone()
        if not reply_check:
            reply_to_id = None

    inserted = db.execute(
        """INSERT INTO messages (chat_id, sender_id, content, message_type, reply_to_id)
           VALUES (?, ?, ?, 'text', ?) RETURNING id""",
        (chat_id, uid, content, reply_to_id),
    ).fetchone()
    db.commit()
    msg_id = inserted["id"]
    row = db.execute(
        """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
           FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (msg_id,),
    ).fetchone()
    payload = serialize_message(db, row)
    db.close()

    socketio.emit("new_message", payload, room=f"chat_{chat_id}")
    return jsonify(payload)


@socketio.on("send_message")
def on_send_message(data, callback=None):
    uid = session.get("user_id")
    if not uid:
        if callback:
            callback({"ok": False, "error": "نشست کاربری منقضی شده است"})
        return
    chat_id = data.get("chat_id")
    content = (data.get("content") or "").strip()
    message_type = data.get("message_type") or "text"
    file_url = data.get("file_url")
    file_name = data.get("file_name")
    file_size = data.get("file_size")
    reply_to_id = data.get("reply_to_id")

    if not chat_id:
        if callback: callback({"ok": False, "error": "گفتگو نامعتبر است"})
        return
    if message_type == "text" and not content:
        if callback: callback({"ok": False, "error": "متن پیام خالی است"})
        return
    if message_type != "text" and not file_url:
        if callback: callback({"ok": False, "error": "فایل پیام نامعتبر است"})
        return

    db = get_db()
    membership = is_member(db, chat_id, uid)
    if not membership:
        db.close()
        if callback: callback({"ok": False, "error": "دسترسی به این گفتگو ندارید"})
        return

    chat = db.execute("SELECT type, open_chat FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not can_post_in_chat(chat, membership):
        db.close()
        socketio.emit("send_error", {"chat_id": chat_id, "reason": "channel_restricted"})
        if callback: callback({"ok": False, "error": "فقط مالک و مدیران این کانال می‌توانند پیام ارسال کنند"})
        return

    # A reply must point to a real message inside the very same chat
    if reply_to_id:
        reply_check = db.execute(
            "SELECT id FROM messages WHERE id=? AND chat_id=?", (reply_to_id, chat_id)
        ).fetchone()
        if not reply_check:
            reply_to_id = None

    inserted = db.execute(
        """INSERT INTO messages (chat_id, sender_id, content, message_type, file_url, file_name, file_size, reply_to_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        (chat_id, uid, content, message_type, file_url, file_name, file_size, reply_to_id),
    ).fetchone()
    db.commit()
    msg_id = inserted["id"]
    row = db.execute(
        """SELECT m.*, u.display_name as sender_name, u.avatar_color as sender_color, u.avatar_url as sender_avatar
           FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (msg_id,),
    ).fetchone()
    payload = serialize_message(db, row)
    db.close()

    # Acknowledge only after the database commit. The client uses this ACK as the
    # authoritative success signal; emit the realtime event afterwards so a
    # transient Socket.IO broadcast problem can never make a saved message look
    # like a failed send.
    if callback:
        callback({"ok": True, "message_id": msg_id})
    socketio.emit("new_message", payload, room=f"chat_{chat_id}")


@socketio.on("join_chat")
def on_join_chat(data):
    uid = session.get("user_id")
    if not uid:
        return
    chat_id = data.get("chat_id")
    db = get_db()
    member = is_member(db, chat_id, uid)
    db.close()
    if member:
        join_room(f"chat_{chat_id}")


@socketio.on("typing")
def on_typing(data):
    uid = session.get("user_id")
    if not uid:
        return
    chat_id = data.get("chat_id")
    socketio.emit("typing", {"chat_id": chat_id, "user_id": uid}, room=f"chat_{chat_id}", include_self=False)


# ---------------------------------------------------------------------------
# WebRTC voice call signaling (the server just relays SDP/ICE between the
# two peers taking the call; the actual audio travels directly between
# browsers once the connection is established)
# ---------------------------------------------------------------------------

@socketio.on("call_offer")
def on_call_offer(data):
    uid = session.get("user_id")
    if not uid:
        return
    to_user_id = data.get("to_user_id")
    sdp = data.get("sdp")
    chat_id = data.get("chat_id")
    if not to_user_id or not sdp:
        return

    if not is_user_online(to_user_id):
        socketio.emit("call_failed", {"reason": "offline", "to_user_id": to_user_id})
        return

    db = get_db()
    caller = db.execute(
        "SELECT display_name, avatar_color, avatar_url FROM users WHERE id=?", (uid,)
    ).fetchone()
    db.close()

    socketio.emit("incoming_call", {
        "from_user_id": uid,
        "from_name": caller["display_name"] if caller else "کاربر",
        "from_avatar_color": caller["avatar_color"] if caller else "#4e89ff",
        "from_avatar_url": caller["avatar_url"] if caller else None,
        "sdp": sdp,
        "chat_id": chat_id,
    }, room=f"user_{to_user_id}")


@socketio.on("call_answer")
def on_call_answer(data):
    uid = session.get("user_id")
    if not uid:
        return
    to_user_id = data.get("to_user_id")
    sdp = data.get("sdp")
    if not to_user_id or not sdp:
        return
    socketio.emit("call_answered", {"sdp": sdp, "from_user_id": uid}, room=f"user_{to_user_id}")


@socketio.on("call_ice_candidate")
def on_call_ice_candidate(data):
    uid = session.get("user_id")
    if not uid:
        return
    to_user_id = data.get("to_user_id")
    candidate = data.get("candidate")
    if not to_user_id or not candidate:
        return
    socketio.emit("call_ice_candidate", {"candidate": candidate, "from_user_id": uid}, room=f"user_{to_user_id}")


@socketio.on("call_end")
def on_call_end(data):
    uid = session.get("user_id")
    if not uid:
        return
    to_user_id = data.get("to_user_id")
    if not to_user_id:
        return
    socketio.emit("call_ended", {"from_user_id": uid}, room=f"user_{to_user_id}")


@socketio.on("call_reject")
def on_call_reject(data):
    uid = session.get("user_id")
    if not uid:
        return
    to_user_id = data.get("to_user_id")
    if not to_user_id:
        return
    socketio.emit("call_rejected", {"from_user_id": uid}, room=f"user_{to_user_id}")


if __name__ == "__main__":
    print("=" * 60)
    print("سرور لینکو (Linko) در حال اجراست")
    print("آدرس: http://127.0.0.1:5000")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
