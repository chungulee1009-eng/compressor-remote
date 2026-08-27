// 최소 서비스워커: 앱 셸 캐시(오프라인 시 UI 골격 유지). API 는 항상 네트워크.
const CACHE = 'comp-shell-v4';
const SHELL = [
  '/', '/alarms',
  '/static/css/style.css',
  '/static/js/common.js', '/static/js/dashboard.js', '/static/js/chart.js',
  '/static/js/unit.js', '/static/js/alarms.js', '/static/js/admin.js',
  '/static/icon.svg', '/manifest.webmanifest',
  '/static/icons/icon-192.png', '/static/icons/apple-touch-icon.png'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request).then(r => {
      const copy = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
      return r;
    }).catch(() => caches.match(e.request).then(m => m || caches.match('/')))
  );
});
