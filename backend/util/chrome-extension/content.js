// Civitai 收藏助手 - Content Script
// 在图片卡片上注入收藏按钮，点击即加入复刻队列

const ADDED_ATTR = 'data-civitai-fav-injected';

// ============ 提取图片 ID ============
function extractImageId(el) {
  // 1. 从卡片内的 <a> 链接提取
  const links = el.querySelectorAll('a[href*="/images/"]');
  for (const a of links) {
    const m = a.href.match(/\/images\/(\d+)/);
    if (m) return m[1];
  }
  // 2. 从卡片自身的 href 提取（卡片本身是 <a>）
  if (el.tagName === 'A' && el.href) {
    const m = el.href.match(/\/images\/(\d+)/);
    if (m) return m[1];
  }
  return null;
}

// ============ 发送到后端 ============
function addToFavorites(imageId, btn) {
  const url = `https://civitai.com/images/${imageId}`;
  btn.dataset.state = 'loading';
  btn.title = '添加中...';
  btn.textContent = '⏳';

  chrome.runtime.sendMessage({ type: 'ADD_FAVORITE', url }, (resp) => {
    if (chrome.runtime.lastError) {
      btn.dataset.state = 'error';
      btn.title = '连接失败，请确认后端已启动';
      btn.textContent = '❌';
      setTimeout(() => resetBtn(btn), 3000);
      return;
    }
    if (resp && (resp.status === 'ok' || resp.status === 'duplicate')) {
      btn.dataset.state = resp.status === 'duplicate' ? 'duplicate' : 'done';
      btn.title = resp.status === 'duplicate' ? '已在收藏队列中' : '已加入收藏队列！';
      btn.textContent = resp.status === 'duplicate' ? '✔' : '★';
      // done 状态不自动重置，保持视觉反馈
    } else {
      btn.dataset.state = 'error';
      btn.title = (resp && resp.message) || '添加失败';
      btn.textContent = '❌';
      setTimeout(() => resetBtn(btn), 3000);
    }
  });
}

function resetBtn(btn) {
  btn.dataset.state = 'idle';
  btn.title = '加入复刻收藏队列';
  btn.textContent = '⬡';
}

// ============ 创建按钮 ============
function createFavBtn(imageId) {
  const btn = document.createElement('button');
  btn.className = 'civitai-fav-btn';
  btn.title = '加入复刻收藏队列';
  btn.textContent = '⬡';
  btn.dataset.state = 'idle';
  btn.dataset.imageId = imageId;

  btn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (btn.dataset.state === 'loading' || btn.dataset.state === 'done') return;
    addToFavorites(imageId, btn);
  });

  return btn;
}

// ============ 注入到卡片 ============
function injectBtn(card) {
  if (card.hasAttribute(ADDED_ATTR)) return;

  const imageId = extractImageId(card);
  if (!imageId) return;

  card.setAttribute(ADDED_ATTR, imageId);

  // 找卡片内的相对定位容器（图片包裹层）
  const wrapper = card.querySelector('[style*="position"]') || card;
  wrapper.style.position = 'relative';

  const btn = createFavBtn(imageId);
  wrapper.appendChild(btn);
}

// ============ 扫描页面上的图片卡片 ============
function scanCards() {
  // Civitai 图片卡片选择器（覆盖列表页、模型页、用户页等）
  const selectors = [
    'a[href*="/images/"]',           // 直接图片链接卡片
    '[class*="card"] a[href*="/images/"]',
    'article a[href*="/images/"]',
  ];

  const seen = new Set();
  for (const sel of selectors) {
    document.querySelectorAll(sel).forEach(el => {
      // 找最近的卡片容器（有 position 的祖先，或直接用 el）
      const card = el.closest('article') || el.closest('[class*="card"]') || el;
      const id = extractImageId(card);
      if (id && !seen.has(id)) {
        seen.add(id);
        injectBtn(card);
      }
    });
  }

  // 图片详情页：单图模式
  const pageMatch = location.href.match(/civitai\.com\/images\/(\d+)/);
  if (pageMatch) {
    const imageId = pageMatch[1];
    const detailKey = `detail-${imageId}`;
    if (!seen.has(detailKey)) {
      seen.add(detailKey);
      injectDetailBtn(imageId);
    }
  }
}

// ============ 图片详情页注入（固定位置） ============
function injectDetailBtn(imageId) {
  const existId = 'civitai-fav-detail-btn';
  if (document.getElementById(existId)) return;

  const btn = createFavBtn(imageId);
  btn.id = existId;
  btn.className = 'civitai-fav-btn civitai-fav-detail';
  btn.title = '加入复刻收藏队列';
  document.body.appendChild(btn);
}

// ============ MutationObserver 监听 SPA 动态渲染 ============
let scanTimer = null;
function scheduleScan() {
  clearTimeout(scanTimer);
  scanTimer = setTimeout(scanCards, 300);
}

const observer = new MutationObserver(scheduleScan);
observer.observe(document.body, { childList: true, subtree: true });

// 监听 SPA 路由变化（Civitai 用 Next.js pushState）
const _pushState = history.pushState.bind(history);
history.pushState = function (...args) {
  _pushState(...args);
  scheduleScan();
};
window.addEventListener('popstate', scheduleScan);

// 初始扫描
scanCards();
