# Linko — PostgreSQL / Render

این نسخه دیتابیس دائمی Linko را از SQLite به PostgreSQL منتقل کرده است.

## Render

اگر از Blueprint استفاده می‌کنی، `render.yaml` شامل Web Service و یک Render Postgres رایگان است و `DATABASE_URL` را خودکار به سرویس وصل می‌کند.

اگر سرویس را دستی ساخته‌ای:
1. یک Render Postgres بساز.
2. در Web Service > Environment متغیر `DATABASE_URL` را روی Connection String دیتابیس قرار بده.
3. Build Command:
   `pip install -r requirements.txt`
4. Start Command:
   `gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 0 backend.app:app`
5. Deploy کن.

## چه چیزهایی دائمی هستند؟

در PostgreSQL نگهداری می‌شوند:
- کاربران و حساب‌ها
- رمزهای هش‌شده
- چت‌ها، گروه‌ها و کانال‌ها
- اعضا و نقش‌ها
- پیام‌های متنی
- پاسخ‌ها و فورواردهای متنی
- ری‌اکشن‌ها
- پین‌ها
- وضعیت خوانده‌شدن / سین
- Poll و رأی‌ها
- تنظیمات پروفایل و تنظیمات کاربر
- last_seen

## چه چیزهایی عمداً دائمی نیستند؟

فایل‌های آپلودی شامل عکس، ویدیو، صوت و فایل معمولی روی دیسک دائمی ذخیره نمی‌شوند.
در شروع هر اجرای جدید:
- فایل‌های موجود در `backend/uploads` پاک می‌شوند.
- پیام‌های وابسته به رسانه از دیتابیس حذف می‌شوند.
- آواتارهای فایل‌محور پاک می‌شوند.

در نتیجه بعد از restart/deploy اگر فایل رسانه‌ای از بین رفته باشد، پیام خراب با لینک شکسته در سایت باقی نمی‌ماند.

## نکته مهم درباره Free Postgres

Free Postgres خود Render برای استفاده آزمایشی/پروژه شخصی است و طبق محدودیت فعلی Render، 1GB فضا دارد و 30 روز بعد از ساخت منقضی می‌شود. بنابراین «باقی ماندن بعد از sleep» درست است، اما Free Postgres برای نگهداری نامحدود داده مناسب نیست.
