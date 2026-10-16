"""
دستیار هوش مصنوعی لینکو (Linko AI Assistant)
================================================

This module is the single place that decides what the AI assistant says
back to a user. Right now it's a rule-based Persian responder (keyword
matching + a bit of arithmetic), intentionally built as a "moderately
trained, Persian-fluent" placeholder — the user explicitly said this will
be upgraded later to a real LLM.

HOW TO UPGRADE TO A REAL LLM LATER
-----------------------------------
1. Set the environment variable AI_API_KEY to a real API key (e.g. an
   Anthropic API key). Optionally set AI_API_PROVIDER (defaults to
   "anthropic").
2. Implement the body of `_call_real_ai()` below. An example using the
   Anthropic Messages API is sketched in its docstring.
3. Nothing else needs to change: `generate_reply()` already prefers
   `_call_real_ai()` whenever AI_API_KEY is set, and only falls back to the
   rule-based engine when it returns None (e.g. on error, or when no key is
   configured). This means the rest of the app (app.py, the frontend) never
   needs to know whether the reply came from a real model or the fallback.
"""

import os
import random
import re
from datetime import datetime

AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_API_PROVIDER = os.environ.get("AI_API_PROVIDER", "anthropic").strip().lower()

ASSISTANT_NAME = "دستیار لینکو"

SYSTEM_PERSONA = (
    "تو «دستیار لینکو» هستی، یک دستیار هوش مصنوعی فارسی‌زبان، دوستانه، مفید و "
    "مختصر که داخل پیام‌رسان لینکو به کاربران کمک می‌کند. علاوه بر گفتگوی عمومی، "
    "باید بتوانی درباره‌ی خود پیام‌رسان لینکو و امکاناتش (گفتگوی خصوصی/گروه/کانال، "
    "پیام‌های ذخیره‌شده، بایگانی رمزدار، نظرسنجی، ری‌اکشن، پین و فوروارد پیام، "
    "پیام صوتی و اشتراک فایل، تیک خوانده‌شدن، تماس صوتی، تنظیمات تم و امنیت حساب) "
    "به‌صورت دقیق و کاربردی راهنمایی بدهی."
)


def generate_reply(history, user_message):
    """Main entry point used by app.py.

    history: list of {"role": "user"|"assistant", "content": str}, oldest
             first (recent conversation context, already trimmed by the
             caller).
    user_message: the new message from the user (str).

    Returns the assistant's reply text (str).
    """
    if AI_API_KEY:
        try:
            real_reply = _call_real_ai(history, user_message)
            if real_reply:
                return real_reply
        except Exception:
            pass  # fall back silently to the rule-based engine

    return _rule_based_reply(history, user_message)


def is_fallback(reply):
    """True when the rule-based engine had no real answer (so the caller may try
    Wikipedia or learned knowledge instead)."""
    return reply in _FALLBACK_REPLIES


def _call_real_ai(history, user_message):
    """Placeholder for a real LLM call. Returns None until implemented.

    Example implementation using the Anthropic Messages API (once the
    `anthropic` package is installed and AI_API_KEY is set):

        import anthropic
        client = anthropic.Anthropic(api_key=AI_API_KEY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        messages.append({"role": "user", "content": user_message})
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            system=SYSTEM_PERSONA,
            messages=messages,
        )
        return response.content[0].text

    Left unimplemented on purpose — this is the "later we'll upgrade it"
    hook the project owner asked for.
    """
    return None


# ---------------------------------------------------------------------------
# Rule-based fallback engine
# ---------------------------------------------------------------------------

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

_MATH_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/xX×÷])\s*(-?\d+(?:\.\d+)?)\s*$"
)

_GREETING_WORDS = ["سلام", "درود", "سلاام", "سلامм", "هی", "هلو", "hi", "hello"]
_HOWAREYOU_WORDS = ["خوبی", "چطوری", "حالت چطوره", "چه خبر", "خوبید"]
_NAME_WORDS = ["اسمت چیه", "اسمت چیست", "تو کی هستی", "خودت رو معرفی کن", "کی هستی"]
_CAPABILITY_WORDS = ["چیکار میکنی", "چه کاری میکنی", "چه کمکی میکنی", "چه کمکی می‌کنی", "چکار میتونی", "چه کاری می‌توانی"]
_THANKS_WORDS = ["ممنون", "مرسی", "متشکرم", "تشکر", "سپاس"]
_BYE_WORDS = ["خداحافظ", "بای", "فعلا", "فعلاً", "می‌رم", "میرم دیگه"]
_TIME_WORDS = ["ساعت چنده", "ساعت چند است", "الان ساعت چنده", "ساعت الان"]
_DATE_WORDS = ["امروز چندشنبه", "تاریخ امروز", "امروز چه روزیه", "امروز چندمه"]

_FALLBACK_REPLIES = [
    "متوجه نشدم، می‌شه کمی بیشتر توضیح بدی؟",
    "هنوز دارم یاد می‌گیرم! می‌تونی سوالت رو به شکل دیگه‌ای بپرسی؟",
    "این یکی رو هنوز بلد نیستم، ولی به‌مرور بهتر می‌شم 🙂",
    "می‌شه واضح‌تر بگی منظورت چیه؟",
]

_GREETING_REPLIES = [
    "سلام! خوش اومدی 🙂 چه کمکی از من ساخته‌ست؟",
    "درود بر تو! چطور می‌تونم کمکت کنم؟",
    "سلام سلام! در خدمتم.",
]

_HOWAREYOU_REPLIES = [
    "من که یه هوش مصنوعی‌ام همیشه سر پا هستم! تو چطوری؟",
    "عالیم، ممنون که پرسیدی. حال تو چطوره؟",
]

_NAME_REPLIES = [
    f"من {ASSISTANT_NAME} هستم، دستیار هوش مصنوعی همین پیام‌رسان لینکو.",
]

_CAPABILITY_REPLIES = [
    "می‌تونم به سوالات ساده جواب بدم، حساب کنم، ساعت و تاریخ رو بگم، باهات گفتگو کنم و درباره‌ی خود لینکو هم راهنماییت کنم — مثلاً بپرس «چطور گروه بسازم؟» یا «بایگانی چیه؟». به‌زودی قابلیت‌های بیشتری هم اضافه می‌شه!",
]

_THANKS_REPLIES = [
    "خواهش می‌کنم! هر وقت کاری داشتی در خدمتم.",
    "قابلی نداشت 🙂",
]

_BYE_REPLIES = [
    "خداحافظ! هر وقت خواستی برگرد.",
    "به امید دیدار دوباره 👋",
]


def _normalize(text):
    return text.translate(_PERSIAN_DIGITS).strip()


def _contains_any(text, words):
    return any(w in text for w in words)


# ---------------------------------------------------------------------------
# Linko knowledge base — lets the assistant answer questions *about the app
# itself* (its own features, how to use them), not just generic chit-chat.
# Each entry is (trigger keywords, answer). The first entry whose keyword
# appears anywhere in the user's message wins, so put more specific /
# distinctive phrasings first within each entry and keep topics narrow.
# ---------------------------------------------------------------------------

_LINKO_FAQ = [
    (
        ["لینکو چیه", "لینکو چیست", "لینکو یعنی چی", "درباره لینکو", "درمورد لینکو", "این برنامه چیه", "این برنامه چیست", "این سایت چیه"],
        "لینکو (Linko) یک پیام‌رسان فارسی شبیه تلگرام/واتس‌اپ است: گفتگوی خصوصی، گروه، کانال، تماس صوتی، ارسال عکس/ویدیو/فایل/پیام صوتی، نظرسنجی، پیام‌های ذخیره‌شده، بایگانی رمزدار، و همین دستیار هوش مصنوعی که الان داری باهاش حرف می‌زنی 🙂 هر سوالی درباره‌ی امکاناتش داشته باشی، بپرس.",
    ),
    (
        ["پیام ذخیره شده", "پیام‌های ذخیره‌شده", "save message", "ذخیره یادداشت"],
        "«پیام‌های ذخیره‌شده» یک فضای شخصی بالای لیست گفتگوهاست (آیکن 🔖) که فقط خودت می‌بینی؛ می‌تونی توش یادداشت متنی بنویسی یا عکس/فایل/ویس ذخیره کنی — دقیقاً مثل یک گفتگوی معمولی، با این تفاوت که برای کس دیگه‌ای ارسال نمی‌شه.",
    ),
    (
        ["دستیار هوش مصنوعی", "هوش مصنوعی لینکو", "ai assistant", "تو چی هستی", "تو کی هستی"],
        f"من {ASSISTANT_NAME} هستم — یک فضای گفتگوی خصوصی (آیکن 🤖) که فقط خودت می‌بینی، دقیقاً مثل «پیام‌های ذخیره‌شده». می‌تونی هر سوالی بپرسی، حساب ساده بخوای، یا درباره‌ی خود لینکو بپرسی. از پایین چت می‌تونی با آیکن اطلاعات (ℹ️) کل تاریخچه‌ی گفتگومون رو هم پاک کنی.",
    ),
    (
        ["بایگانی", "آرشیو", "archive"],
        "«بایگانی» یک فضای رمزدار و جدا برای گفتگوهای خصوصی‌تر است (آیکن 📦). برای استفاده ازش، اول باید از پروفایل و تنظیمات ← حساب کاربری، یک رمز مخصوص بایگانی تعیین کنی (این رمز با رمز ورود حسابت فرق داره)؛ بعد با همون رمز هر بار قفلش رو باز می‌کنی.",
    ),
    (
        ["ساخت گروه", "گروه بسازم", "چطور گروه", "ایجاد گروه"],
        "برای ساخت گروه، روی دکمه‌ی «+» یا «گفتگوی جدید» در بالای لیست گفتگوها بزن، گزینه‌ی «گروه» رو انتخاب کن، اسم گروه و اعضا رو مشخص کن و بساز. بعداً هم می‌تونی از «اطلاعات گفتگو» (آیکن ℹ️) عضو اضافه/حذف کنی یا نقش‌ها (مالک/مدیر/عضو) رو مدیریت کنی.",
    ),
    (
        ["ساخت کانال", "کانال بسازم", "چطور کانال", "ایجاد کانال"],
        "برای ساخت کانال، از همون دکمه‌ی «گفتگوی جدید»، گزینه‌ی «کانال» رو انتخاب کن. در کانال فقط مالک و مدیران می‌تونن پیام بفرستن (مگر این‌که حالت «باز» کانال رو فعال کنی تا همه بتونن پیام بدن).",
    ),
    (
        ["نظرسنجی", "poll", "آزمون", "quiz"],
        "داخل گروه‌ها و کانال‌ها می‌تونی نظرسنجی یا آزمون بسازی: روی دکمه‌ی نظرسنجی کنار کادر پیام بزن، سوال و گزینه‌ها رو وارد کن. در حالت «آزمون» هم می‌تونی گزینه‌ی درست رو مشخص کنی تا بعد از رأی دادن، درست/غلط بودن نشون داده بشه.",
    ),
    (
        ["ری اکشن", "ری‌اکشن", "reaction", "ایموجی روی پیام"],
        "برای گذاشتن ری‌اکشن، روی هر پیامی بزن و از ردیف ایموجی‌هایی که باز می‌شه، یکی رو انتخاب کن. هر کاربر فقط می‌تونه یک ری‌اکشن روی هر پیام بذاره.",
    ),
    (
        ["پین کردن", "پین پیام", "پیام پین", "pin message"],
        "برای پین‌کردن یک پیام در گروه یا کانال (فقط مالک/مدیر)، روی پیام بزن و از منو گزینه‌ی «پین» رو انتخاب کن. پیام‌های پین‌شده بالای صفحه‌ی گفتگو نشون داده می‌شن.",
    ),
    (
        ["فوروارد", "forward"],
        "برای فوروارد یک پیام، روی پیام بزن، گزینه‌ی «فوروارد» رو انتخاب کن و یک یا چند گفتگو (یا حتی پیام‌های ذخیره‌شده‌ی خودت) رو برای ارسال انتخاب کن.",
    ),
    (
        ["پیام صوتی", "ویس بفرستم", "ضبط صدا", "voice message"],
        "برای ارسال پیام صوتی، روی آیکن میکروفون کنار کادر پیام بزن، نگه دار تا ضبط بشه. توجه: پیام‌های صوتی در لینکو فقط قابل پخش‌اند و هیچ‌جای برنامه دکمه‌ی دانلود ندارن.",
    ),
    (
        ["حجم فایل", "آپلود فایل", "چقدر فایل", "سقف فایل", "محدودیت فایل"],
        "می‌تونی عکس، ویدیو، ویس یا هر فایلی رو با آیکن گیره‌کاغذ ارسال کنی؛ حداکثر حجم مجاز هر فایل ۶۰ مگابایت است.",
    ),
    (
        ["تیک", "خونده شد", "خوانده شد", "دو تیک", "read receipt"],
        "یک تیک یعنی پیامت ارسال شده ولی هنوز توسط طرف مقابل خونده نشده؛ دو تیک (رنگی) یعنی پیام خونده شده. تا وقتی پیام در حال ارسال است هم یک آیکن ساعت کوچک نشونش می‌دیم.",
    ),
    (
        ["آنلاین", "آخرین بازدید", "last seen", "online"],
        "وضعیت آنلاین/آخرین بازدید هر کاربر بالای صفحه‌ی گفتگوی خصوصی با اون شخص نشون داده می‌شه.",
    ),
    (
        ["تماس صوتی", "زنگ بزنم", "تماس بگیرم", "voice call", "video call", "تماس تصویری"],
        "در گفتگوهای خصوصی می‌تونی با آیکن تلفن بالای صفحه، تماس صوتی برقرار کنی (از فناوری WebRTC استفاده می‌شه، بدون نیاز به سرور صدا).",
    ),
    (
        ["تم", "رنگ برنامه", "حالت تاریک", "حالت روشن", "theme", "dark mode"],
        "از پروفایل و تنظیمات می‌تونی رنگ تم و حالت روشن/تاریک برنامه رو عوض کنی؛ این تنظیمات برای حساب کاربریت ذخیره می‌شه و با ورود از هر دستگاهی هم اعمال می‌شه.",
    ),
    (
        ["رمز عبور", "پسوردم", "امنیت حساب", "password"],
        "رمز عبور حساب‌ها با الگوریتم امن هش می‌شه و هیچ‌وقت به‌صورت متن ساده ذخیره نمی‌شه. برای تغییر رمز هم می‌تونی از پروفایل و تنظیمات ← حساب کاربری اقدام کنی.",
    ),
]


def _match_linko_faq(text):
    for keywords, answer in _LINKO_FAQ:
        if _contains_any(text, keywords):
            return answer
    return None


def _rule_based_reply(history, user_message):
    text = _normalize(user_message)
    lowered = text.lower()

    math_match = _MATH_RE.match(text)
    if math_match:
        a, op, b = math_match.groups()
        try:
            a, b = float(a), float(b)
            if op in ("+",):
                result = a + b
            elif op in ("-",):
                result = a - b
            elif op in ("*", "x", "X", "×"):
                result = a * b
            elif op in ("/", "÷"):
                if b == 0:
                    return "نمی‌شه بر صفر تقسیم کرد!"
                result = a / b
            else:
                result = None
            if result is not None:
                if result == int(result):
                    result = int(result)
                return f"نتیجه می‌شه: {result}"
        except (ValueError, ZeroDivisionError):
            pass

    if _contains_any(lowered, _TIME_WORDS):
        now = datetime.now().strftime("%H:%M")
        return f"الان ساعت {now} است."

    if _contains_any(lowered, _DATE_WORDS):
        now = datetime.now().strftime("%Y-%m-%d")
        return f"تاریخ امروز (میلادی): {now}"

    faq_answer = _match_linko_faq(lowered)
    if faq_answer:
        return faq_answer

    if _contains_any(lowered, _NAME_WORDS):
        return random.choice(_NAME_REPLIES)

    if _contains_any(lowered, _CAPABILITY_WORDS):
        return random.choice(_CAPABILITY_REPLIES)

    if _contains_any(lowered, _THANKS_WORDS):
        return random.choice(_THANKS_REPLIES)

    if _contains_any(lowered, _BYE_WORDS):
        return random.choice(_BYE_REPLIES)

    if _contains_any(lowered, _HOWAREYOU_WORDS):
        return random.choice(_HOWAREYOU_REPLIES)

    if _contains_any(lowered, _GREETING_WORDS):
        return random.choice(_GREETING_REPLIES)

    return random.choice(_FALLBACK_REPLIES)
