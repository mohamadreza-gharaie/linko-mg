"""
اتصال دستیار لینکو به ویکی‌پدیای فارسی
=========================================

وقتی دستیار جواب سوالی را از قبل نمی‌داند، همین ماژول با کتابخانه‌ی
`Wikipedia-API` در fa.wikipedia.org جست‌وجو می‌کند و خلاصه‌ی مقاله‌ی مرتبط را
برمی‌گرداند. نتیجه در جدول `ai_wiki_cache` ذخیره می‌شود تا سوال تکراری دوباره
به اینترنت نرود (این اطلاعات عمومی است، پس بین همه‌ی کاربران مشترک است).

هیچ‌وقت نباید جریان گفتگو را خراب کند: هر خطای شبکه/کتابخانه یعنی `None`.

متغیرهای محیطی (همه اختیاری):
  WIKIPEDIA_ENABLED   اگر "0" باشد اتصال به ویکی‌پدیا خاموش می‌شود
  WIKIPEDIA_LANG      زبان ویکی‌پدیا (پیش‌فرض fa)
  WIKIPEDIA_TIMEOUT   timeout درخواست به ثانیه (پیش‌فرض 6)
"""

import os
import re

try:
    import wikipediaapi
except ImportError:  # pragma: no cover
    wikipediaapi = None

ENABLED = os.environ.get("WIKIPEDIA_ENABLED", "1").strip() != "0"
LANG = os.environ.get("WIKIPEDIA_LANG", "fa").strip() or "fa"
try:
    TIMEOUT = float(os.environ.get("WIKIPEDIA_TIMEOUT", "6"))
except ValueError:
    TIMEOUT = 6.0

USER_AGENT = "LinkoMessenger/1.0 (https://linko.onrender.com; AI assistant)"
MAX_SUMMARY_CHARS = 700

_client = None

# کلمات پرسشی/اضافی که نباید داخل عبارت جست‌وجو بروند
_NOISE_PATTERNS = [
    r"چیست", r"چیه", r"چی هست", r"چی است", r"چی بود", r"کیست", r"کیه", r"کی بود",
    r"کجاست", r"کجاس", r"کجا هست", r"چه کسی", r"چه کسیه", r"چند سال", r"چجوری",
    r"درباره(?:‌| )?ی", r"درباره", r"راجع به", r"راجب", r"توضیح بده", r"توضیح",
    r"بگو", r"بگید", r"میخوام بدونم", r"می‌خوام بدونم", r"می‌خواهم بدانم",
    r"بدونم", r"اطلاعات", r"تعریف", r"معنی", r"معنای", r"لطفا", r"لطفاً",
    r"ویکی ?پدیا", r"سرچ کن", r"جستجو کن", r"جست‌وجو کن", r"پیدا کن",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS))
_PUNCT_RE = re.compile(r"[؟?!.,،:;«»\"'()\[\]{}]")
_SPACE_RE = re.compile(r"\s+")


def _get_client():
    global _client
    if _client is None:
        _client = wikipediaapi.Wikipedia(user_agent=USER_AGENT, language=LANG, timeout=TIMEOUT)
    return _client


def extract_topic(question):
    """«کوروش بزرگ کیست؟» -> «کوروش بزرگ»"""
    text = _PUNCT_RE.sub(" ", question or "")
    text = _NOISE_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:120]


def cache_key(topic):
    return _SPACE_RE.sub(" ", (topic or "").replace("‌", " ")).strip().lower()


def _trim_summary(text):
    text = (text or "").strip()
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    cut = text[:MAX_SUMMARY_CHARS]
    # تا آخرین جمله‌ی کامل ببُر
    last = max(cut.rfind(". "), cut.rfind("؟"), cut.rfind("!"), cut.rfind(".\n"))
    if last > 200:
        cut = cut[: last + 1]
    return cut.rstrip() + ("" if cut.endswith((".", "؟", "!")) else "…")


def fetch_from_wikipedia(topic):
    """جست‌وجوی زنده. خروجی: {"title","summary","url"} یا None."""
    if not ENABLED or wikipediaapi is None or not topic:
        return None
    try:
        wiki = _get_client()
        title = None
        page = wiki.page(topic)
        if page.exists():
            title = page.title
        else:
            found = wiki.search(topic, limit=1)
            titles = list(found.pages.keys()) if found and found.pages else []
            if titles:
                title = titles[0]
        if not title:
            return None
        page = wiki.page(title)
        if not page.exists():
            return None
        summary = _trim_summary(page.summary)
        if not summary:
            return None
        return {"title": page.title, "summary": summary, "url": page.fullurl}
    except Exception:
        return None


def lookup(db, question):
    """اول کش، بعد ویکی‌پدیا. خروجی: dict یا None."""
    topic = extract_topic(question)
    if len(topic) < 2:
        return None
    key = cache_key(topic)

    row = db.execute(
        "SELECT title, summary, url FROM ai_wiki_cache WHERE query_key=?", (key,)
    ).fetchone()
    if row:
        return {"title": row["title"], "summary": row["summary"], "url": row["url"]}

    result = fetch_from_wikipedia(topic)
    if not result:
        return None
    try:
        db.execute(
            """INSERT INTO ai_wiki_cache (query_key, title, summary, url)
               VALUES (?, ?, ?, ?) ON CONFLICT (query_key) DO NOTHING""",
            (key, result["title"], result["summary"], result["url"]),
        )
        db.commit()
    except Exception:
        pass
    return result


def format_answer(result):
    return (
        f"📚 طبق ویکی‌پدیا درباره‌ی «{result['title']}»:\n\n{result['summary']}\n\n"
        f"منبع: {result['url']}"
    )
