const CACHE = "gmd-shell-v1.3.0";
const SHELL = [
  "/",
  "/index.html",
  "/assets/tailwind.css?v=1.3.0",
  "/assets/app.css?v=1.3.0",
  "/assets/htmx.min.js?v=4.0.0-beta6",
  "/assets/app.js?v=1.3.0",
  "/favicon.svg?v=1.3.0",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ui/")) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(event.request);
        if (cached) return cached;
        if (event.request.mode === "navigate") return caches.match("/index.html");
        return new Response("Offline", { status: 503, statusText: "Offline" });
      }),
  );
});
