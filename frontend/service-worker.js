/* ============================================================
   鹦鹉早报 Service Worker - 离线缓存
   策略：
     1. 静态资源（HTML/CSS/JS/Manifest）→ Cache First
     2. 新闻 API 请求 → Network First，失败回退缓存
     3. 新闻数据缓存保留最近 3 天
   ============================================================ */

// 缓存名：更新版本号可触发客户端重新缓存
const CACHE_VERSION = 'v1.0.0';
const STATIC_CACHE = `parrot-static-${CACHE_VERSION}`;
const NEWS_CACHE = 'parrot-news-cache';    // 与前端 JS 中保持一致

// 预缓存的静态资源列表（相对路径，匹配 start_url）
const STATIC_ASSETS = [
  './',
  './index.html',
  './style.css',
  './manifest.json'
];

// ============================================================
// 安装：预缓存静态资源
// ============================================================
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        console.log('[SW] 预缓存静态资源:', STATIC_ASSETS);
        return cache.addAll(STATIC_ASSETS);
      })
      .then(() => self.skipWaiting())  // 立即接管，不等旧 SW 退出
      .catch((err) => console.error('[SW] 预缓存失败:', err))
  );
});

// ============================================================
// 激活：清理旧版本缓存
// ============================================================
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        const deletions = cacheNames
          .filter((name) => {
            // 静态缓存：非当前版本全部删除
            const isStatic = name.startsWith('parrot-static-');
            if (isStatic) return name !== STATIC_CACHE;
            // 新闻缓存：保留，由前端 JS 按日期清理
            return false;
          })
          .map((name) => {
            console.log('[SW] 删除旧缓存:', name);
            return caches.delete(name);
          });
        return Promise.all(deletions);
      })
      .then(() => self.clients.claim())  // 立即接管所有页面
      .then(() => console.log('[SW] 激活完成'))
  );
});

// ============================================================
// 工具：判断是否是新闻 API 请求
// ============================================================
function isNewsApiRequest(url) {
  // Supabase REST API 路径，或前端自定义的 news-cache-*.json
  return url.includes('/rest/v1/news') || url.includes('news-cache-');
}

// ============================================================
// 工具：是否是静态资源请求
// ============================================================
function isStaticAssetRequest(url) {
  return url.endsWith('.html')
      || url.endsWith('.css')
      || url.endsWith('.js')
      || url.endsWith('.json')
      || url.endsWith('.svg')
      || url.endsWith('.png')
      || url.endsWith('.jpg')
      || url.endsWith('.ico');
}

// ============================================================
// Cache First 策略（用于静态资源）
// ============================================================
async function cacheFirstStrategy(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const networkResp = await fetch(request);
    if (networkResp && networkResp.status === 200) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, networkResp.clone());
    }
    return networkResp;
  } catch (err) {
    // 完全离线且无缓存时，返回 index.html（SPA 模式兜底）
    return caches.match('./index.html');
  }
}

// ============================================================
// Network First 策略（用于新闻 API）
//  1. 先尝试网络
//  2. 成功 → 更新缓存 + 返回
//  3. 失败 → 从 NEWS_CACHE 找任何可用的新闻缓存兜底
// ============================================================
async function networkFirstStrategy(request) {
  try {
    const networkResp = await fetch(request);
    if (networkResp && networkResp.status === 200) {
      const cache = await caches.open(NEWS_CACHE);
      cache.put(request, networkResp.clone());
    }
    return networkResp;
  } catch (err) {
    console.warn('[SW] 新闻网络请求失败，回退缓存:', err);
    const cache = await caches.open(NEWS_CACHE);
    const keys = await cache.keys();

    // 按缓存 URL 时间倒序（假设 URL 含日期），取第一个命中
    const sortedKeys = keys
      .filter((req) => req.url.includes('news-cache-') || req.url.includes('/rest/v1/news'))
      .sort((a, b) => (a.url < b.url ? 1 : -1));

    for (const key of sortedKeys) {
      const cachedResp = await cache.match(key);
      if (cachedResp) return cachedResp;
    }

    // 没有任何新闻缓存，抛出错误让前端继续处理
    throw err;
  }
}

// ============================================================
// 主拦截逻辑
// ============================================================
self.addEventListener('fetch', (event) => {
  // 只处理 GET 请求
  if (event.request.method !== 'GET') return;

  const url = event.request.url;

  // 新闻 API → Network First
  if (isNewsApiRequest(url)) {
    event.respondWith(networkFirstStrategy(event.request));
    return;
  }

  // 静态资源（同源）→ Cache First
  const selfOrigin = self.location.origin;
  if (url.startsWith(selfOrigin) && isStaticAssetRequest(url)) {
    event.respondWith(cacheFirstStrategy(event.request));
    return;
  }

  // 其他请求（跨域图片、字体等）：直接走网络，失败则静默
});

// ============================================================
// 消息接口：前端可主动触发更新
// ============================================================
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data && event.data.type === 'CLEAR_NEWS_CACHE') {
    event.waitUntil(
      caches.delete(NEWS_CACHE).then(() => console.log('[SW] 新闻缓存已清理'))
    );
  }
});
