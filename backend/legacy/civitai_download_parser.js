/**
 * Civitai Model Download URL Parser
 * 目标：将 Civitai API 下载地址解析为真正的 Cloudflare R2 下载地址
 */

const net = require('net');
const tls = require('tls');
const http = require('http');
const { createConnection } = require('net');

// 代理配置 - 尝试 HTTP 代理
const PROXY = {
  host: '127.0.0.1',
  port: 10020
};

async function fetchWithHttpProxy(url) {
  return new Promise((resolve, reject) => {
    console.log(`\n=== Civitai Download Parser ===`);
    console.log(`API URL: ${url}`);
    console.log(`使用 HTTP 代理: ${PROXY.host}:${PROXY.port}`);
    
    const urlObj = new URL(url);
    
    const options = {
      host: PROXY.host,
      port: PROXY.port,
      path: url,
      method: 'GET',
      headers: {
        'Host': urlObj.hostname,
        'User-Agent': 'Mozilla/5.0',
        'Accept': '*/*',
        'Proxy-Connection': 'Keep-Alive'
      }
    };
    
    http.get(options, (res) => {
      console.log(`\n代理响应状态: ${res.statusCode}`);
      console.log(`响应头:`, res.headers);
      
      const location = res.headers.location;
      if (location) {
        console.log(`\n✅ 重定向到:`);
        console.log(location);
        
        const domain = new URL(location).hostname;
        console.log(`\n📦 存储提供商: ${domain.includes('cloudflarestorage') ? 'Cloudflare R2' : domain}`);
      } else {
        console.log(`\n❌ 无重定向`);
      }
      
      let body = '';
      res.on('data', (chunk) => body += chunk);
      res.on('end', () => {
        console.log(`\n响应体: ${body.substring(0, 200)}`);
        resolve();
      });
    }).on('error', reject);
  });
}

async function fetchWithHttpsProxy(url) {
  return new Promise((resolve, reject) => {
    console.log(`\n=== Civitai Download Parser ===`);
    console.log(`API URL: ${url}`);
    console.log(`使用 HTTPS 代理: ${PROXY.host}:${PROXY.port}`);
    
    const urlObj = new URL(url);
    
    // HTTPS 代理使用 CONNECT 方法
    const connectReq = http.request({
      host: PROXY.host,
      port: PROXY.port,
      method: 'CONNECT',
      path: `${urlObj.hostname}:${urlObj.port || 443}`
    }, (res) => {
      // 建立隧道后，不直接使用 res，需要新连接
    });
    
    connectReq.end();
    
    connectReq.on('connect', (res, socket, head) => {
      console.log(`\n代理隧道建立: ${res.statusCode}`);
      
      const tlsSocket = tls.connect({
        socket: socket,
        host: urlObj.hostname
      }, () => {
        const request = `GET ${urlObj.pathname}${urlObj.search} HTTP/1.1\r\n` +
          `Host: ${urlObj.hostname}\r\n` +
          `User-Agent: Mozilla/5.0\r\n` +
          `Accept: */*\r\n` +
          `Referer: https://civitai.com/\r\n` +
          `Connection: close\r\n\r\n`;
        
        tlsSocket.write(request);
        
        let response = '';
        tlsSocket.on('data', (chunk) => {
          response += chunk;
          
          if (response.includes('\r\n\r\n')) {
            const parts = response.split('\r\n\r\n');
            const headers = parts[0];
            
            console.log(`\n响应头:`);
            console.log(headers);
            
            const locationMatch = headers.match(/Location:\s*(.+)/i);
            if (locationMatch) {
              const redirectUrl = locationMatch[1].trim();
              console.log(`\n✅ 重定向到:`);
              console.log(redirectUrl);
              
              const domain = new URL(redirectUrl).hostname;
              console.log(`\n📦 存储提供商: ${domain.includes('cloudflarestorage') ? 'Cloudflare R2' : domain}`);
            } else {
              console.log(`\n❌ 无重定向`);
            }
            
            tlsSocket.end();
            socket.end();
            resolve();
          }
        });
        
        tlsSocket.on('error', reject);
      });
      
      tlsSocket.on('error', reject);
    });
    
    connectReq.on('error', reject);
  });
}

async function parseCivitaiDownloadUrl(modelId) {
  const url = `https://civitai.com/api/download/models/${modelId}?type=Model&format=SafeTensor&size=pruned&fp=fp16`;
  
  // 先尝试 HTTPS 代理 CONNECT 方法
  try {
    await fetchWithHttpsProxy(url);
  } catch (e) {
    console.log(`\nHTTPS 代理失败: ${e.message}`);
    console.log(`\n尝试 HTTP 代理...`);
    await fetchWithHttpProxy(url);
  }
}

const testId = process.argv[2] || '2579173';
parseCivitaiDownloadUrl(testId).catch(console.error);
