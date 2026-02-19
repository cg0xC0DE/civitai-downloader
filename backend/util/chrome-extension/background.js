// 后端地址（通过 nginx 代理访问，避免直连端口的 CORS 问题）
const BACKEND_URL = 'http://localhost/civitaidl-service';

// ============ 后端请求（供 content script 和右键菜单共用） ============
async function callAddFavorite(url) {
  const res = await fetch(`${BACKEND_URL}/api/favorite/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  });
  return res.json();
}

// ============ 监听来自 content script 的消息 ============
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type !== 'ADD_FAVORITE') return false;
  callAddFavorite(msg.url)
    .then(data => sendResponse(data))
    .catch(() => sendResponse({ status: 'error', message: '连接失败，请确认 nginx + 后端已启动' }));
  return true; // 保持 sendResponse 异步有效
});

// ============ 右键菜单（保留作为备用） ============
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'add-to-fav-queue',
    title: '⭐ 加入复刻收藏队列',
    contexts: ['page', 'link', 'image'],
    documentUrlPatterns: ['*://civitai.com/*']
  });
});

function extractImageUrl(info, tab) {
  for (const url of [info.linkUrl, info.srcUrl, info.pageUrl, tab && tab.url]) {
    if (!url) continue;
    const m = url.match(/civitai\.com\/images\/(\d+)/);
    if (m) return `https://civitai.com/images/${m[1]}`;
  }
  return null;
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== 'add-to-fav-queue') return;
  const imageUrl = extractImageUrl(info, tab);
  if (!imageUrl) {
    showNotification(tab.id, '❌ 无法识别图片 URL', '请在图片页面或图片链接上右键');
    return;
  }
  callAddFavorite(imageUrl)
    .then(data => {
      if (data.status === 'ok' || data.status === 'duplicate') {
        showNotification(tab.id,
          data.status === 'duplicate' ? '⚠️ 已在队列中' : '✅ 已加入收藏队列',
          imageUrl
        );
      } else {
        showNotification(tab.id, '❌ 添加失败', data.message || '未知错误');
      }
    })
    .catch(() => showNotification(tab.id, '❌ 连接失败', '请确认 nginx + 后端已启动'));
});

// 在页面中显示通知（注入内容脚本）
function showNotification(tabId, title, message) {
  chrome.scripting.executeScript({
    target: { tabId },
    func: (title, message) => {
      // 移除旧通知
      const old = document.getElementById('__aesthetic_toast');
      if (old) old.remove();

      const div = document.createElement('div');
      div.id = '__aesthetic_toast';
      div.innerHTML = `<strong>${title}</strong><br><span style="font-size:12px;opacity:0.85;">${message}</span>`;
      Object.assign(div.style, {
        position: 'fixed',
        top: '20px',
        right: '20px',
        zIndex: '999999',
        background: title.includes('✅') ? '#1a7a3a' : '#c0392b',
        color: '#fff',
        padding: '12px 20px',
        borderRadius: '8px',
        fontSize: '14px',
        lineHeight: '1.5',
        boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
        transition: 'opacity 0.3s',
        maxWidth: '360px',
        wordBreak: 'break-all',
      });
      document.body.appendChild(div);
      setTimeout(() => {
        div.style.opacity = '0';
        setTimeout(() => div.remove(), 400);
      }, 2500);
    },
    args: [title, message]
  });
}
