# Civitai 美学分析助手 - Chrome 插件

## 功能

在 civitai.com 页面上右键，出现「加入美学分析队列」菜单项。
点击后，自动提取当前图片 URL 并发送到后端 `/api/favorite/add` 接口。

## 安装

1. 打开 Chrome，访问 `chrome://extensions/`
2. 打开右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择本文件夹 (`util/chrome-extension`)

## 图标

首次使用需要提供 3 个 PNG 图标文件：
- `icon16.png` (16x16)
- `icon48.png` (48x48)
- `icon128.png` (128x128)

可以用任意图片工具生成，或者删除 `manifest.json` 中的 `icons` 字段使用默认图标。

## 配置

后端地址默认为 `http://localhost:53133`，如需修改请编辑 `background.js` 第 2 行。

## 使用

1. 确保后端服务已启动 (`start_backend.cmd`)
2. 在 civitai.com 的图片页面或图片链接上右键
3. 点击「加入美学分析队列」
4. 页面右上角会显示成功/失败通知
