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
    "مختصر که داخل پیام‌رسان لینکو به کاربران کمک می‌کند."
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
    "می‌تونم به سوالات ساده جواب بدم، حساب کنم، ساعت و تاریخ رو بگم و باهات گفتگو کنم. به‌زودی قابلیت‌های بیشتری هم اضافه می‌شه!",
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
