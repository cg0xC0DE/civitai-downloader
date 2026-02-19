# Civitai 收藏助手 - Chrome 插件

## 功能

在 civitai.com 页面上右键，出现「⭐ 加入复刻收藏队列」菜单项。
点击后，自动提取当前图片 URL 并发送到后端 `/api/favorite/add` 接口。

支持以下右键场景：
- 在图片详情页（`civitai.com/images/xxxxxx`）右键页面空白处
- 在图片列表页右键图片链接
- 在图片列表页右键图片本身

## 前置条件

- nginx 已启动（后端通过 `http://localhost/civitaidl-service` 访问）
- 后端服务已启动（`start_backend.cmd`）

## 安装

1. 打开 Chrome，访问 `chrome://extensions/`
2. 打开右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择本文件夹（`backend/util/chrome-extension`）

**注意**：每次修改 `background.js` 后需要在 `chrome://extensions/` 页面点击插件的刷新按钮。

## 配置

后端地址默认为 `http://localhost/civitaidl-service`（通过 nginx 代理）。
如需修改，编辑 `background.js` 第 2 行的 `BACKEND_URL`。

## 使用

1. 确保 nginx 和后端服务均已启动
2. 在 civitai.com 的图片页面或图片链接上右键
3. 点击「⭐ 加入复刻收藏队列」
4. 页面右上角会显示成功/失败/重复通知
