#!/usr/bin/env node
/**
 * Civitai Downloader 健康检查脚本
 * 三条验证链路:
 * 1. ngrok → 前端 (验证 ngrok 隧道)
 * 2. nginx → 前端 + 后端 (验证 nginx 路由)
 * 3. localhost → 前端 + 后端 (验证本地服务)
 */

const http = require('http');
const https = require('https');

const NGROK_URL = 'https://dentiled-gennie-stichometrical.ngrok-free.dev';
const NGINX_URL = 'http://localhost:80';
const FRONTEND_NGINX = NGINX_URL + '/civitaidl/';
const BACKEND_NGINX = NGINX_URL + '/civitaidl-service/api/';
const FRONTEND_LOCAL = 'http://localhost:53134';
const BACKEND_LOCAL = 'http://localhost:53133';

// 验证关键词
const KEYWORDS = {
  frontend: ['civitai downloader', '下载模型'],
  backend: ['status', 'ok']
};

// 端口配置
const PORTS = {
  nginx: 80,
  frontend: 53134,
  backend: 53133,
  ngrok: 4040
};

function httpGet(url, timeout = 10000) {
  return new Promise((resolve) => {
    const isHttps = url.startsWith('https');
    const client = isHttps ? https : http;

    const req = client.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('error', err => resolve({ error: err.message }));
    req.setTimeout(timeout, () => {
      req.destroy();
      resolve({ error: 'timeout' });
    });
  });
}

async function isPortOpen(port) {
  return new Promise((resolve) => {
    const socket = require('net').Socket();
    socket.setTimeout(2000);
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('timeout', () => { socket.destroy(); resolve(false); });
    socket.on('error', () => { resolve(false); });
    socket.connect(port, '127.0.0.1');
  });
}

function validateContent(name, status, body) {
  if (status !== 200) {
    return { valid: false, reason: `HTTP ${status}` };
  }

  const keywords = KEYWORDS[name];
  if (!keywords || keywords.length === 0) return { valid: true };

  const bodyLower = (body || '').toLowerCase();

  // 检查 ngrok 错误页面
  if (bodyLower.includes('ngrok') && (bodyLower.includes('error') || bodyLower.includes('not found'))) {
    return { valid: false, reason: 'ngrok 错误页面' };
  }

  // 检查关键词
  const hasKeyword = keywords.some(kw => bodyLower.includes(kw.toLowerCase()));
  if (!hasKeyword) {
    return { valid: false, reason: `缺少关键词 (${keywords.join(', ')})` };
  }

  return { valid: true };
}

async function checkService(name, url, checkPort = null, keywords = null) {
  console.log(`  [check] ${name}: ${url}`);

  // 检查端口
  if (checkPort && !(await isPortOpen(checkPort))) {
    console.log(`       ❌ 端口 ${checkPort} 未开放`);
    return { name, ok: false, reason: `端口 ${checkPort} 未开放` };
  }

  // 检查 HTTP
  const result = await httpGet(url, 10000);
  if (result.error) {
    console.log(`       ❌ 请求失败: ${result.error}`);
    return { name, ok: false, reason: result.error };
  }

  // 验证内容
  const validation = validateContentWithKeywords(name, result.status, result.body, keywords);
  if (!validation.valid) {
    console.log(`       ❌ ${validation.reason}`);
    return { name, ok: false, reason: validation.reason };
  }

  console.log(`       ✅ HTTP ${result.status}`);
  return { name, ok: true };
}

function validateContentWithKeywords(name, status, body, keywords) {
  if (status !== 200) {
    return { valid: false, reason: `HTTP ${status}` };
  }

  if (!keywords || keywords.length === 0) return { valid: true };

  const bodyLower = (body || '').toLowerCase();

  if (bodyLower.includes('ngrok') && (bodyLower.includes('error') || bodyLower.includes('not found'))) {
    return { valid: false, reason: 'ngrok 错误页面' };
  }

  const hasKeyword = keywords.some(kw => bodyLower.includes(kw.toLowerCase()));
  if (!hasKeyword) {
    return { valid: false, reason: `缺少关键词 (${keywords.join(', ')})` };
  }

  return { valid: true };
}

async function startService(name) {
  const scripts = {
    backend: { cmd: 'cmd /c "C:/workplace/civitai-downloader/start_backend.cmd"', cwd: 'C:/workplace/civitai-downloader' },
    frontend: { cmd: 'cmd /c "C:/workplace/civitai-downloader/start_frontend.cmd"', cwd: 'C:/workplace/civitai-downloader' },
    nginx: { cmd: 'C:/nginx-1.28.1/nginx.exe', args: ['-c', 'C:/nginx-1.28.1/conf/nginx.conf'], cwd: 'C:/nginx-1.28.1' },
    ngrok: { cmd: 'cmd', args: ['/c', 'set', 'HTTP_PROXY=&', 'set', 'HTTPS_PROXY=&', 'ngrok.exe', 'http', '80'], cwd: 'C:/workplace' }
  };

  const cfg = scripts[name];
  if (!cfg) {
    console.log(`[healthcheck] 未知服务: ${name}`);
    return false;
  }

  console.log(`\n[healthcheck] 启动 ${name}...`);
  try {
    const { spawn } = require('child_process');
    const proc = spawn(cfg.cmd, cfg.args || [], {
      cwd: cfg.cwd,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      shell: true
    });
    proc.unref();
    console.log(`[healthcheck] ✓ ${name} 已启动 (PID: ${proc.pid})`);
    return true;
  } catch (e) {
    console.log(`[healthcheck] ❌ ${name} 启动失败: ${e.message}`);
    return false;
  }
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function healthCheck() {
  const ts = new Date().toISOString();
  console.log(`\n${'='.repeat(60)}`);
  console.log(`[healthcheck] [${ts}] Civitai Downloader 健康检查`);
  console.log('='.repeat(60));

  let hasIssue = false;

  // === 链路 1: ngrok 隧道验证 ===
  console.log('\n--- 链路 1: ngrok 隧道 ---');
  const ngrokFrontend = await checkService('ngrok_frontend', NGROK_URL + '/civitaidl/', PORTS.ngrok, ['civitai']);
  if (!ngrokFrontend.ok) hasIssue = true;

  // === 链路 2: nginx 路由验证 (ngrok → frontend/backend) ===
  console.log('\n--- 链路 2: nginx 路由 ---');
  const nginxFrontend = await checkService('nginx_frontend', FRONTEND_NGINX, PORTS.nginx, ['civitai']);
  const nginxBackend = await checkService('nginx_backend', BACKEND_NGINX + 'status', PORTS.nginx, ['status']);
  if (!nginxFrontend.ok || !nginxBackend.ok) hasIssue = true;

  // === 链路 3: 本地服务验证 (nginx + frontend/backend) ===
  console.log('\n--- 链路 3: 本地服务 ---');
  const localFrontend = await checkService('local_frontend', FRONTEND_LOCAL, PORTS.frontend, ['civitai']);
  const localBackend = await checkService('local_backend', BACKEND_LOCAL + '/api/status', PORTS.backend, ['status']);
  if (!localFrontend.ok || !localBackend.ok) hasIssue = true;

  // 汇总结果
  console.log('\n' + '='.repeat(60));
  console.log('[healthcheck] 检查结果:');
  console.log('='.repeat(60));

  const results = {
    ngrok_tunnel: ngrokFrontend,
    nginx_frontend: nginxFrontend,
    nginx_backend: nginxBackend,
    local_frontend: localFrontend,
    local_backend: localBackend
  };

  for (const [name, r] of Object.entries(results)) {
    const status = r.ok ? '✅' : '❌';
    console.log(`  ${status} ${name}: ${r.ok ? '正常' : r.reason || '失败'}`);
  }

  // 自动修复
  if (hasIssue) {
    console.log(`\n[healthcheck] 尝试自动修复...`);

    // 优先启动 nginx (它代理所有流量)
    if (!nginxFrontend.ok || !nginxBackend.ok) {
      startService('nginx');
      await wait(2000);
    }

    // 启动前端和后端
    if (!localFrontend.ok) {
      startService('frontend');
      await wait(2000);
    }
    if (!localBackend.ok) {
      startService('backend');
      await wait(2000);
    }

    // 启动 ngrok
    if (!ngrokFrontend.ok) {
      startService('ngrok');
      await wait(3000);
    }

    // 重新验证
    console.log(`\n[healthcheck] 重新验证...`);
    const recheckNginx = await checkService('nginx_recheck', FRONTEND_NGINX, PORTS.nginx, ['civitai']);
    const recheckBackend = await checkService('backend_recheck', BACKEND_LOCAL + '/api/status', PORTS.backend, ['status']);
    const recheckFrontend = await checkService('frontend_recheck', FRONTEND_LOCAL, PORTS.frontend, ['civitai']);
    const recheckNgrok = await checkService('ngrok_recheck', NGROK_URL + '/civitaidl/', PORTS.ngrok, ['civitai']);

    if (recheckNginx.ok && recheckBackend.ok && recheckFrontend.ok && recheckNgrok.ok) {
      console.log(`\n[healthcheck] ✅ 所有服务已恢复！`);
    } else {
      console.log(`\n[healthcheck] ⚠️ 部分服务仍异常，请手动检查`);
      if (!recheckNginx.ok) console.log('  - nginx: 检查 nginx.conf 配置');
      if (!recheckBackend.ok) console.log('  - 后端: 检查 start_backend.cmd');
      if (!recheckFrontend.ok) console.log('  - 前端: 检查 start_frontend.cmd');
      if (!recheckNgrok.ok) console.log('  - ngrok: 检查 ngrok 隧道');
    }
  } else {
    console.log(`\n[healthcheck] ✅ 所有链路验证通过！`);
  }

  console.log(`\n${'='.repeat(60)}\n`);
}

healthCheck().catch(console.error);
