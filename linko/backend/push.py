"""
نوتیفیکیشن Push لینکو (Web Push)
====================================

Lets Linko notify a user about a new message even when the site/tab is
completely closed, using the standard Web Push API (service worker +
VAPID). This module owns the VAPID keypair and the actual "send a push"
call; the subscribe/unsubscribe HTTP endpoints and the "a new message just
arrived, notify the other members" call site live in app.py.

VAPID KEYS
----------
A VAPID keypair identifies *this server* to the push services (Chrome's,
Firefox's, etc.) — it is not a per-user secret. For it to survive restarts
and redeploys (so a user's subscription keeps working instead of silently
breaking the next time the service redeploys), set these two environment
variables on Render once, using the values this module prints to the logs
the first time it has to generate a keypair for itself:

  VAPID_PRIVATE_KEY_PEM   the full PEM text (multi-line — Render's
                          environment variable editor accepts multi-line
                          values directly)
  VAPID_SUBJECT           a "mailto:you@example.com" or "https://..." URL
                          identifying the site owner (required by the Web
                          Push spec, shown to push services, not to users)

If VAPID_PRIVATE_KEY_PEM is not set, a keypair is generated once per process
start instead — push notifications still work for the lifetime of that
running instance, but every subscription becomes invalid on the next
restart/redeploy (browsers would need to resubscribe, which the frontend
does automatically the next time a logged-in user opens the site).
"""

import json
import os

from py_vapid import Vapid01
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import base64

try:
    from pywebpush import webpush, WebPushException
except ImportError:  # pragma: no cover - only hit if the dependency is missing
    webpush = None
    WebPushException = Exception

from backend.database import get_db

VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

_vapid = Vapid01()
_private_key_pem = os.environ.get("VAPID_PRIVATE_KEY_PEM")
if _private_key_pem:
    _vapid.from_pem(_private_key_pem.encode())
else:
    _vapid.generate_keys()
    print(
        "[push] No VAPID_PRIVATE_KEY_PEM set — generated a temporary VAPID "
        "keypair for this process. Push subscriptions will break on the next "
        "restart/redeploy unless you set these two environment variables "
        "permanently on Render:\n"
        f"  VAPID_SUBJECT=mailto:you@example.com (or https://yourdomain)\n"
        "  VAPID_PRIVATE_KEY_PEM=\n" + _vapid.private_pem().decode()
    )

VAPID_PRIVATE_KEY_PEM = _vapid.private_pem().decode()
VAPID_PUBLIC_KEY_B64URL = base64.urlsafe_b64encode(
    _vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
).rstrip(b"=").decode()


def is_configured():
    return webpush is not None


def save_subscription(db, user_id, endpoint, p256dh, auth):
    db.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (endpoint) DO UPDATE SET user_id=EXCLUDED.user_id, p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth""",
        (user_id, endpoint, p256dh, auth),
    )
    db.commit()


def remove_subscription(db, user_id, endpoint):
    db.execute(
        "DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",
        (user_id, endpoint),
    )
    db.commit()


def _send_one(db, sub, payload_json):
    try:
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=payload_json,
            vapid_private_key=VAPID_PRIVATE_KEY_PEM,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=60,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            # The push service says this subscription no longer exists
            # (user revoked notifications, uninstalled, cleared data, ...) —
            # stop trying to deliver to it.
            db.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
            db.commit()
    except Exception:
        # Never let a push delivery failure break the message-sending flow
        # that triggered it.
        pass


def notify_users(user_ids, title, body, url=None, tag=None):
    """Send a Web Push notification to every given user's subscribed devices."""
    if not is_configured() or not user_ids:
        return
    payload_json = json.dumps({"title": title, "body": body, "url": url or "/", "tag": tag})
    db = get_db()
    try:
        for uid in user_ids:
            subs = db.execute(
                "SELECT * FROM push_subscriptions WHERE user_id=?", (uid,)
            ).fetchall()
            for sub in subs:
                _send_one(db, sub, payload_json)
    finally:
        db.close()


def notify_chat_members(chat_id, sender_id, title, body, url=None, tag=None):
    """Push-notify every member of a chat except the sender."""
    if not is_configured():
        return
    db = get_db()
    try:
        rows = db.execute(
            "SELECT user_id FROM chat_members WHERE chat_id=? AND user_id != ?",
            (chat_id, sender_id),
        ).fetchall()
        member_ids = [r["user_id"] for r in rows]
    finally:
        db.close()
    notify_users(member_ids, title, body, url=url, tag=tag)
