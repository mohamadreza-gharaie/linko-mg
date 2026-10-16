"""
یادگیری دستیار از گفتگو با کاربر
==================================

دستیار فقط از گفتگوی خود کاربر با «دستیار هوش مصنوعی» یاد می‌گیرد — هرگز از
پیام‌های خصوصی بین کاربران، گروه‌ها یا کانال‌ها چیزی نمی‌خواند یا ذخیره نمی‌کند.
آموخته‌ها برای هر کاربر خصوصی‌اند (جدول `ai_knowledge`)، چون اگر بین همه
مشترک بود هم اطلاعات شخصی لو می‌رفت و هم هر کسی می‌توانست جواب‌های غلط به همه
یاد بدهد.

دستورها (داخل گفتگوی دستیار):
  یاد بگیر: سوال = جواب        (یا با  =>  یا  ->  یا  :)
  فراموش کن: سوال
  فراموش کن همه
  چی یاد گرفتی؟
"""

import re

MAX_QUESTION_CHARS = 200
MAX_ANSWER_CHARS = 1000
MAX_ENTRIES_PER_USER = 200
SIMILARITY_THRESHOLD = 0.75

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_CHAR_FIX = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "‌": " "})
_PUNCT_RE = re.compile(r"[؟?!.,،:;«»\"'()\[\]{}\-_]")
_SPACE_RE = re.compile(r"\s+")
_FILLER = {"لطفا", "لطفاً", "یه", "یک", "را", "رو", "که", "ی", "هست", "است", "ه", "چیه", "چیست", "کیه", "کیست", "چی"}

_LEARN_RE = re.compile(r"^\s*(?:یاد\s*بگیر|یادبگیر|یاد\s*بگیرید)\s*[:：\-]?\s*(.+)$", re.S)
_SEPARATORS = ["=>", "->", "=", "؛", ":"]
_FORGET_ALL_RE = re.compile(r"^\s*(?:فراموش\s*کن|پاک\s*کن)\s*(?:همه|همه‌چیز|همه چیز|تمام)\s*[.!]?\s*$")
_FORGET_RE = re.compile(r"^\s*فراموش\s*کن\s*[:：\-]?\s*(.+)$", re.S)
_LIST_RE = re.compile(r"(?:چی|چه\s*چیزی?|چه)\s*(?:رو\s*)?یاد\s*گرفت[یه]|آموخته‌?های?\s*من|یادگرفته‌?هات")


def normalize(text):
    text = (text or "").translate(_DIGITS).translate(_CHAR_FIX)
    text = _PUNCT_RE.sub(" ", text).lower()
    return _SPACE_RE.sub(" ", text).strip()


def _tokens(text):
    return {t for t in normalize(text).split(" ") if t and t not in _FILLER}


def _similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _split_question_answer(body):
    for sep in _SEPARATORS:
        if sep in body:
            q, a = body.split(sep, 1)
            if q.strip() and a.strip():
                return q.strip(), a.strip()
    return None, None


def find_answer(db, user_id, question):
    """نزدیک‌ترین آموخته‌ی این کاربر به سوال؛ خروجی: متن جواب یا None."""
    key = normalize(question)
    if not key:
        return None
    rows = db.execute(
        "SELECT question, question_key, answer FROM ai_knowledge WHERE user_id=?", (user_id,)
    ).fetchall()
    best, best_score = None, 0.0
    for r in rows:
        if r["question_key"] == key:
            return r["answer"]
        score = _similarity(question, r["question"])
        if score > best_score:
            best, best_score = r, score
    if best is not None and best_score >= SIMILARITY_THRESHOLD:
        return best["answer"]
    return None


def handle_command(db, user_id, text):
    """اگر پیام یک دستور یادگیری/فراموشی بود پاسخ می‌دهد، وگرنه None."""
    if _FORGET_ALL_RE.match(text):
        db.execute("DELETE FROM ai_knowledge WHERE user_id=?", (user_id,))
        db.commit()
        return "همه‌ی چیزهایی که از تو یاد گرفته بودم فراموش شد 🧹"

    m = _LEARN_RE.match(text)
    if m:
        question, answer = _split_question_answer(m.group(1))
        if not question:
            return "برای یاد دادن این‌طوری بنویس:\nیاد بگیر: سوال = جواب\nمثلاً: یاد بگیر: پایتخت ایران = تهران"
        if len(question) > MAX_QUESTION_CHARS or len(answer) > MAX_ANSWER_CHARS:
            return "سوال یا جواب خیلی طولانی است؛ کوتاه‌ترش کن."
        key = normalize(question)
        if not key:
            return "سوال معتبر نیست."
        existing = db.execute(
            "SELECT 1 FROM ai_knowledge WHERE user_id=? AND question_key=?", (user_id, key)
        ).fetchone()
        if not existing:
            count = db.execute(
                "SELECT COUNT(*) AS c FROM ai_knowledge WHERE user_id=?", (user_id,)
            ).fetchone()["c"]
            if count >= MAX_ENTRIES_PER_USER:
                return f"حافظه‌ی من برای تو پر شده ({MAX_ENTRIES_PER_USER} مورد). با «فراموش کن: سوال» جا باز کن."
        db.execute(
            """INSERT INTO ai_knowledge (user_id, question, question_key, answer)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (user_id, question_key)
               DO UPDATE SET question=EXCLUDED.question, answer=EXCLUDED.answer""",
            (user_id, question, key, answer),
        )
        db.commit()
        return f"یاد گرفتم ✅\n«{question}» ← {answer}\n(این را فقط برای تو به خاطر می‌سپارم.)"

    m = _FORGET_RE.match(text)
    if m:
        key = normalize(m.group(1))
        cur = db.execute(
            "DELETE FROM ai_knowledge WHERE user_id=? AND question_key=? RETURNING id", (user_id, key)
        ).fetchall()
        db.commit()
        return "فراموشش کردم." if cur else "چنین چیزی یاد نگرفته بودم."

    if _LIST_RE.search(text):
        rows = db.execute(
            "SELECT question, answer FROM ai_knowledge WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,)
        ).fetchall()
        if not rows:
            return "هنوز چیزی از تو یاد نگرفته‌ام. با «یاد بگیر: سوال = جواب» می‌توانی یادم بدهی."
        lines = [f"• {r['question']} ← {r['answer']}" for r in rows]
        return "این‌ها را یاد گرفته‌ام:\n" + "\n".join(lines)

    return None
