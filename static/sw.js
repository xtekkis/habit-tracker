// Bump this on every commit that touches style.css, templates, or any file
// in STATIC_ASSETS below. The fetch handler's stale-while-revalidate only
// refreshes the cache in the background *after* serving whatever's already
// cached, so an unchanged CACHE_NAME means installed PWA users keep briefly
// seeing one-version-old assets on every load instead of getting the fresh
// ones immediately via a real cache reset.
const CACHE_NAME = 'habit-tracker-v7';
const STATIC_ASSETS = [
    '/static/style.css',
    '/static/lucide.min.js',
    '/static/hanken-grotesk.woff2',
    '/manifest.json',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/icon-maskable.png',
];

self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (e) => {
    if (e.request.method !== 'GET') return;
    const url = new URL(e.request.url);

    if (url.pathname.startsWith('/static/')) {
        // Stale-while-revalidate: serve cache immediately, refresh it from the
        // network in the background so updated assets appear on the next load.
        e.respondWith(
            caches.open(CACHE_NAME).then(cache =>
                cache.match(e.request).then(cached => {
                    const network = fetch(e.request).then(res => {
                        cache.put(e.request, res.clone());
                        return res;
                    }).catch(() => cached);
                    return cached || network;
                })
            )
        );
        return;
    }

    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
