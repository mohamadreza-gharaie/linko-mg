// سرویس‌ورکر لینکو — نمایش نوتیفیکیشن حتی وقتی سایت/تب کاملاً بسته است.
// این فایل جدا از script.js اجرا می‌شود (در ترد مرورگر، نه ترد صفحه) و فقط
// دو کار انجام می‌دهد: ۱) وقتی سرور یک پوش ارسال می‌کند، نوتیفیکیشن نشان بده.
// ۲) وقتی کاربر روی نوتیفیکیشن کلیک کرد، صفحه‌ی لینکو را باز/فوکوس کن.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "لینکو", body: event.data ? event.data.text() : "پیام جدید" };
  }

  const title = data.title || "لینکو";
  const options = {
    body: data.body || "پیام جدید دریافت شد",
    icon: "/favicon.svg",
    badge: "/favicon.svg",
    tag: data.tag || "linko-message",
    data: { url: data.url || "/" },
    dir: "rtl",
    lang: "fa",
  };

  event.waitUntil(
    (async () => {
      // اگر خود کاربر همین الان دارد با همان گفتگو کار می‌کند (تب باز و در
      // فوکوس است)، نیازی به نوتیفیکیشن تکراری نیست؛ خود صفحه پیام را نشان
      // می‌دهد. در غیر این صورت (تب بسته، در پس‌زمینه، یا گفتگوی دیگری باز
      // است) نوتیفیکیشن سیستم‌عامل را نشان بده.
      const allClients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const hasFocusedClient = allClients.some((c) => c.focused);
      if (hasFocusedClient) {
        return;
      }
      await self.registration.showNotification(title, options);
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    (async () => {
      const allClients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of allClients) {
        if ("focus" in client) {
          await client.focus();
          if ("navigate" in client) {
            try {
              await client.navigate(targetUrl);
            } catch (e) {
              // برخی مرورگرها navigate را برای کلاینت‌های غیرهم‌مبدا رد می‌کنند؛
              // در آن صورت همان صفحه‌ی فوکوس‌شده کافی است.
            }
          }
          return;
        }
      }
      if (self.clients.openWindow) {
        await self.clients.openWindow(targetUrl);
      }
    })()
  );
});
