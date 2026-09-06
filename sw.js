const CACHE="timeclock-v1";
const FILES=["./","./index.html","./manifest.json","./icon.svg"];
self.addEventListener("install", e=>{ e.waitUntil(caches.open(CACHE).then(c=>c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener("activate", e=>{ e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", e=>{ e.respondWith(caches.match(e.request).then(r=> r || fetch(e.request))); });
