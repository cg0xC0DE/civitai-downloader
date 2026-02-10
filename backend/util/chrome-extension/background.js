// 后端地址
const BACKEND_URL = 'http://localhost:53133';

// 创建右键菜单
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'add-to-aesthetic-queue',
    title: '加入美学分析队列',
    contexts: ['page', 'link', 'image'],
    documentUrlPatterns: ['*://civitai.com/*']
  });
});

// 从 URL 中提取 Civitai 图片地址
function extractImageUrl(info, tab) {
  // 优先使用右键点击的链接
  if (info.linkUrl) {
    const match = info.linkUrl.match(/civitai\.com\/images\/(\d+)/);
    if (match) return `https://civitai.com/images/${match[1]}`;
  }

  // 其次从当前页面 URL 提取
  if (tab && tab.url) {
    const match = tab.url.match(/civitai\.com\/images\/(\d+)/);
    if (match) return `https://civitai.com/images/${match[1]}`;
  }

  return null;
}

// 右键菜单点击处理
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== 'add-to-aesthetic-queue') return;

  const imageUrl = extractImageUrl(info, tab);
  if (!imageUrl) {
    // 非图片页面，尝试用当前页面 URL
    if (tab && tab.url && tab.url.includes('civitai.com')) {
      showNotification(tab.id, '❌ 无法识别图片 URL', '请在图片页面或图片链接上右键');
    }
    return;
  }

  // 调用后端 API
  fetch(`${BACKEND_URL}/api/favorite/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: imageUrl })
  })
    .then(res => res.json())
    .then(data => {
      if (data.status === 'ok') {
        showNotification(tab.id, '✅ 已加入队列', imageUrl);
      } else {
        showNotification(tab.id, '❌ 添加失败', data.message || '未知错误');
      }
    })
    .catch(err => {
      showNotification(tab.id, '❌ 连接失败', '请确认后端服务已启动');
    });
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
