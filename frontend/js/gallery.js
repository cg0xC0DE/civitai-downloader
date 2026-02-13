/**
 * Azure Blob Gallery - 图片画廊
 * 查询并展示 Azure Blob 中的绘图结果
 */

class Gallery {
    constructor(apiBase) {
        this.apiBase = apiBase;
        this.blobs = [];
        this.tracking = {};  // batch_id -> { favorite_id, source_url }
        this.currentIndex = 0;
        this.pageSize = 20;
        this.isLoading = false;
        this.isDeleting = false;
        this.isAnalyzing = false;
        this.compareMode = true;
        this._compareCache = {}; // imageId -> { thumb_url, width, height }
        this.init();
    }

    init() {
        // DOM 元素
        this.els = {
            galleryTab: document.getElementById('gallery'),
            refreshBtn: document.getElementById('galleryRefreshBtn'),
            prevBtn: document.getElementById('galleryPrevBtn'),
            nextBtn: document.getElementById('galleryNextBtn'),
            counter: document.getElementById('galleryCounter'),
            imageContainer: document.getElementById('galleryImageContainer'),
            imageInfo: document.getElementById('galleryImageInfo'),
            emptyState: document.getElementById('galleryEmptyState'),
        };

        // 绑定事件
        this.els.refreshBtn.addEventListener('click', () => this.loadBlobs());
        this.els.prevBtn.addEventListener('click', () => this.prevImage());
        this.els.nextBtn.addEventListener('click', () => this.nextImage());

        // 初始加载
        this.loadBlobs();
    }

    async loadBlobs() {
        if (this.isLoading) return;

        this.isLoading = true;
        this.showLoading(true);
        this.els.emptyState.style.display = 'none';
        this.els.imageContainer.innerHTML = '';

        try {
            const response = await fetch(`${this.apiBase}/api/azure/list`, {
                headers: { 'ngrok-skip-browser-warning': '1' }
            });
            const data = await response.json();

            if (data.success && data.blobs) {
                // 按时间倒序（最新的在前）
                this.blobs = data.blobs;
                this.tracking = data.tracking || {};
                this.currentIndex = 0;
                this.render();
            } else {
                this.showError(data.message || '加载失败');
            }
        } catch (error) {
            console.error('Load blobs error:', error);
            this.showError('网络错误，请重试');
        } finally {
            this.isLoading = false;
            this.showLoading(false);
        }
    }

    showLoading(show) {
        const loading = document.getElementById('galleryLoading');
        if (loading) {
            loading.style.display = show ? 'block' : 'none';
        }
    }

    showError(message) {
        this.els.imageContainer.innerHTML = `
            <div class="gallery-error">
                <p>❌ ${message}</p>
                <button onclick="gallery.loadBlobs()">重试</button>
            </div>
        `;
    }

    render() {
        if (this.blobs.length === 0) {
            this.els.emptyState.style.display = 'block';
            this.els.imageContainer.innerHTML = '';
            this.els.imageInfo.style.display = 'none';
            this.updateCounter();
            return;
        }

        this.els.emptyState.style.display = 'none';
        this.els.imageInfo.style.display = '';
        this.renderCurrentImage();
        this.updateCounter();
        this.updateButtons();
    }

    renderCurrentImage() {
        if (this.currentIndex < 0 || this.currentIndex >= this.blobs.length) {
            return;
        }

        const url = this.blobs[this.currentIndex];
        const filename = this.extractFilename(url);
        const blobPath = this.extractBlobPath(url);

        // 从 URL 提取信息
        const info = this.parseFilename(filename);

        // 追踪：从文件名提取 batch_id，查找关联的收藏
        const batchId = filename.split('_')[0] || '';
        const trackInfo = this.tracking[batchId] || null;
        const favId = trackInfo ? trackInfo.favorite_id : '';
        const favSourceUrl = trackInfo ? trackInfo.source_url : '';
        // 比对模式（需要有来源 URL）或单图模式
        if (this.compareMode && favSourceUrl) {
            this._renderCompare(url, favSourceUrl, filename);
        } else {
            this.els.imageContainer.innerHTML = `
                <img src="${url}" alt="${filename}" class="gallery-image" onload="gallery.onImageLoad()"
                     onerror="gallery.onImageError()">
            `;
        }

        // 追踪按钮行
        const compareBtn = favSourceUrl ? `
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #06b6d4; border-color: #06b6d4; min-width: 0;"
                        onclick="gallery.toggleCompare()">
                    ${this.compareMode ? '🖼️ 单图模式' : '🔀 原图比对'}
                </button>` : '';
        const trackingRow = favId ? `
            <div class="gallery-info-item" style="border: none; padding-top: 8px; display: flex; gap: 8px; flex-wrap: wrap;">
                ${compareBtn}
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #2563eb; border-color: #2563eb; min-width: 0;"
                        onclick="gallery.goToFavorite('${favId}')">
                    ⭐ 查看来源收藏
                </button>
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #f59e0b; border-color: #f59e0b; min-width: 0;"
                        onclick="gallery.markFavFail('${favId}')">
                    ❌ 标记复刻失败
                </button>
            </div>` : '';

        this.els.imageInfo.innerHTML = `
            <div class="gallery-info-item">
                <span class="gallery-info-label">文件名</span>
                <span class="gallery-info-value">${filename}</span>
            </div>
            ${info.date ? `
                <div class="gallery-info-item">
                    <span class="gallery-info-label">日期</span>
                    <span class="gallery-info-value">${info.date}</span>
                </div>
            ` : ''}
            ${favSourceUrl ? `
                <div class="gallery-info-item">
                    <span class="gallery-info-label">来源</span>
                    <span class="gallery-info-value"><a href="${favSourceUrl}" target="_blank" style="color:var(--accent);">${favSourceUrl.length > 50 ? favSourceUrl.slice(0, 50) + '...' : favSourceUrl}</a></span>
                </div>
            ` : ''}
            <div class="gallery-info-item" style="border: none; padding-top: 12px; display: flex; gap: 8px;">
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #dc2626; border-color: #dc2626;"
                        onclick="gallery.deleteBlob('${blobPath}')" ${this.isDeleting ? 'disabled' : ''}>
                    ${this.isDeleting ? '删除中...' : '🗑️ 删除'}
                </button>
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #7c3aed; border-color: #7c3aed;"
                        onclick="gallery.showAnalyzeModal()" ${this.isAnalyzing ? 'disabled' : ''}>
                    ${this.isAnalyzing ? '⏳ 分析中...' : '🔍 美学分析'}
                </button>
            </div>
            ${trackingRow}
        `;
    }

    extractFilename(url) {
        try {
            const parts = url.split('/');
            return parts[parts.length - 1];
        } catch {
            return 'unknown.png';
        }
    }

    extractBlobPath(url) {
        // 提取 blob 路径，如 "generated/2026-02-09/xxx.png"
        // URL 格式: https://chatarchive.blob.core.windows.net/civitaidl/generated/xxx.png
        try {
            const parts = url.split('/');
            // 跳过 protocol, empty, chatarchive.blob.core.windows.net, civitaidl
            return parts.slice(4).join('/');
        } catch {
            return '';
        }
    }

    async deleteBlob(blobPath) {
        if (!confirm(`确定要删除这张图片吗？\n\n${blobPath}`)) return;

        this.isDeleting = true;
        this.renderCurrentImage(); // 更新按钮状态

        try {
            const response = await fetch(`${this.apiBase}/api/azure/delete`, {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'ngrok-skip-browser-warning': '1'
                },
                body: JSON.stringify({ path: blobPath })
            });
            const result = await response.json();

            if (result.success) {
                // 删除成功，移除当前图片
                this.blobs.splice(this.currentIndex, 1);
                if (this.currentIndex >= this.blobs.length) {
                    this.currentIndex = Math.max(0, this.blobs.length - 1);
                }
                this.render();
                showToast('已删除');
            } else {
                showToast('删除失败: ' + result.error, 'error');
            }
        } catch (error) {
            console.error('Delete error:', error);
            showToast('删除失败: ' + error.message, 'error');
        } finally {
            this.isDeleting = false;
            if (this.blobs.length > 0) {
                this.renderCurrentImage();
            }
        }
    }

    parseFilename(filename) {
        // 格式: generated/YYYY-MM-DD/batch_prompt_index.png
        // 或: generated/test_timestamp.png
        const match = filename.match(/(\d{4}-\d{2}-\d{2})/);
        return {
            date: match ? match[1] : null,
        };
    }

    updateCounter() {
        const total = this.blobs.length;
        const current = total > 0 ? this.currentIndex + 1 : 0;
        this.els.counter.textContent = `${current} / ${total}`;
    }

    updateButtons() {
        const hasPrev = this.currentIndex > 0;
        const hasNext = this.currentIndex < this.blobs.length - 1;

        this.els.prevBtn.disabled = !hasPrev;
        this.els.nextBtn.disabled = !hasNext;
    }

    prevImage() {
        if (this.currentIndex > 0) {
            this.currentIndex--;
            this.renderCurrentImage();
            this.updateCounter();
            this.updateButtons();
        }
    }

    nextImage() {
        if (this.currentIndex < this.blobs.length - 1) {
            this.currentIndex++;
            this.renderCurrentImage();
            this.updateCounter();
            this.updateButtons();
        }
    }

    onImageLoad() {
        // 图片加载成功
    }

    toggleCompare() {
        this.compareMode = !this.compareMode;
        this.renderCurrentImage();
    }

    async _renderCompare(myUrl, sourceUrl, filename) {
        // 从 source_url 提取 image_id
        const m = sourceUrl.match(/\/images\/(\d+)/);
        if (!m) {
            this.els.imageContainer.innerHTML = `<img src="${myUrl}" alt="${filename}" class="gallery-image">`;
            return;
        }
        const imageId = m[1];

        // 显示加载中
        this.els.imageContainer.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-secondary);"><span class="loading-spinner"></span> 加载原图中...</div>`;

        // 获取原图（带缓存）
        let orig = this._compareCache[imageId];
        if (!orig) {
            try {
                const res = await fetch(`${this.apiBase}/api/image/thumb?id=${imageId}`, {
                    headers: { 'ngrok-skip-browser-warning': '1' }
                });
                orig = await res.json();
                if (orig.status === 'ok') this._compareCache[imageId] = orig;
            } catch (e) {
                showToast('获取原图失败: ' + e.message, 'error');
                this.els.imageContainer.innerHTML = `<img src="${myUrl}" alt="${filename}" class="gallery-image">`;
                return;
            }
        }
        if (!orig || !orig.thumb_url) {
            showToast('无法获取原图', 'error');
            this.els.imageContainer.innerHTML = `<img src="${myUrl}" alt="${filename}" class="gallery-image">`;
            return;
        }

        // 用原图宽高比大的 URL
        const origUrl = orig.thumb_url.replace(/\/width=\d+/, '/width=1024');
        const isPortrait = (orig.height || 0) > (orig.width || 0);

        // 竖版左右并列，横版上下并列
        const dir = isPortrait ? 'row' : 'column';
        this.els.imageContainer.innerHTML = `
            <div style="display:flex; flex-direction:${dir}; gap:4px; width:100%; align-items:center;">
                <div style="flex:1; min-width:0; text-align:center; position:relative;">
                    <div style="position:absolute;top:6px;left:6px;background:#0008;color:#fff;font-size:10px;padding:2px 8px;border-radius:10px;z-index:1;">我的作品</div>
                    <img src="${myUrl}" alt="我的作品" style="width:100%;border-radius:8px;display:block;">
                </div>
                <div style="flex:1; min-width:0; text-align:center; position:relative;">
                    <div style="position:absolute;top:6px;left:6px;background:#0008;color:#fff;font-size:10px;padding:2px 8px;border-radius:10px;z-index:1;">原图</div>
                    <img src="${origUrl}" alt="原图" style="width:100%;border-radius:8px;display:block;">
                </div>
            </div>
        `;
    }

    onImageError() {
        this.els.imageContainer.innerHTML = `
            <div class="gallery-error">
                <p>❌ 图片加载失败</p>
                <p class="gallery-error-url">${this.blobs[this.currentIndex]}</p>
            </div>
        `;
    }

    async markFavFail(favId) {
        if (!favId) return;
        if (!confirm('确定标记为"复刻失败"并删除当前图片？')) return;
        try {
            // 1. 标记收藏为 fail
            const res = await fetch(`${this.apiBase}/api/favorite/update-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                body: JSON.stringify({ id: favId, status: 'fail' })
            });
            const data = await res.json();
            if (data.status !== 'ok') {
                showToast('标记失败: ' + (data.message || ''), 'error');
                return;
            }

            // 2. 删除当前图片
            const url = this.blobs[this.currentIndex];
            if (url) {
                const blobPath = this.extractBlobPath(url);
                await fetch(`${this.apiBase}/api/azure/delete`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                    body: JSON.stringify({ path: blobPath })
                });
                this.blobs.splice(this.currentIndex, 1);
                if (this.currentIndex >= this.blobs.length) {
                    this.currentIndex = Math.max(0, this.blobs.length - 1);
                }
                this.render();
            }
            showToast('已标记失败并删除');
        } catch (err) {
            showToast('请求失败: ' + err.message, 'error');
        }
    }

    goToFavorite(favId) {
        if (!favId) return;
        // 切换到收藏 tab
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.querySelector('.tab-btn[data-tab="favorites"]').classList.add('active');
        document.getElementById('favorites').classList.add('active');
        // 通知 app 跳转到指定收藏
        if (window.app && window.app._favAllItems) {
            // 先切到「全部」筛选
            window.app._setFavFilter('all');
            const idx = window.app._favItems.findIndex(i => i.id === favId);
            if (idx >= 0) {
                window.app._favIndex = idx;
                window.app.favRenderCurrent();
            } else {
                // 可能未加载，触发加载
                window.app.loadFavorites();
            }
        }
    }

    showAnalyzeModal() {
        // 移除已有弹窗
        const old = document.getElementById('aestheticModal');
        if (old) old.remove();

        const imageUrl = this.blobs[this.currentIndex];

        const modal = document.createElement('div');
        modal.id = 'aestheticModal';
        modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999;';
        modal.innerHTML = `
            <div style="background:#1e1e2e;border-radius:12px;padding:24px;width:90%;max-width:500px;color:#e0e0e0;">
                <h3 style="margin:0 0 16px;font-size:16px;">🔍 美学分析</h3>
                <div id="aestheticLoading" style="text-align:center;padding:12px;color:#999;">
                    <span>⏳ 检查缓存...</span>
                </div>
                <div id="aestheticInputArea" style="display:none;">
                    <p style="font-size:13px;color:#999;margin-bottom:8px;">请输入你对这张图片的主观感受（可选）：</p>
                    <textarea id="aestheticUserInput" rows="4"
                        style="width:100%;background:#2a2a3e;border:1px solid #444;border-radius:8px;color:#e0e0e0;padding:10px;font-size:13px;resize:vertical;box-sizing:border-box;"
                        placeholder="例如：色彩对比很强，光影氛围很好..."></textarea>
                </div>
                <div id="aestheticResult" style="display:none;margin-top:12px;max-height:300px;overflow-y:auto;"></div>
                <div style="display:flex;gap:10px;margin-top:16px;">
                    <button id="aestheticCancelBtn" class="btn btn-outline btn-sm" style="flex:1;"
                            onclick="document.getElementById('aestheticModal').remove();">关闭</button>
                    <button id="aestheticSubmitBtn" class="btn btn-sm" style="flex:1;background:#7c3aed;color:#fff;border:none;display:none;"
                            onclick="gallery.runAnalysis();">开始分析</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.remove();
        });

        // 先查缓存
        this._checkCachedResult(imageUrl);
    }

    async _checkCachedResult(imageUrl) {
        const loadingDiv = document.getElementById('aestheticLoading');
        const inputArea = document.getElementById('aestheticInputArea');
        const resultDiv = document.getElementById('aestheticResult');
        const submitBtn = document.getElementById('aestheticSubmitBtn');

        try {
            const res = await fetch(`${this.apiBase}/api/aesthetic/result?image_url=${encodeURIComponent(imageUrl)}`, {
                headers: { 'ngrok-skip-browser-warning': '1' }
            });
            const data = await res.json();

            if (data.status === 'success' && data.blueprint) {
                // 已有缓存结果，直接展示
                loadingDiv.style.display = 'none';
                this._renderBlueprint(data.blueprint, resultDiv);
                submitBtn.style.display = 'block';
                submitBtn.textContent = '🔄 重新分析';
                submitBtn.style.background = '#4b5563';
                submitBtn.dataset.force = 'true';
            } else {
                // 无缓存，显示输入区域
                loadingDiv.style.display = 'none';
                inputArea.style.display = 'block';
                submitBtn.style.display = 'block';
                document.getElementById('aestheticUserInput').focus();
            }
        } catch {
            loadingDiv.style.display = 'none';
            inputArea.style.display = 'block';
            submitBtn.style.display = 'block';
        }
    }

    _renderBlueprint(bp, container) {
        container.style.display = 'block';
        container.innerHTML = `
            <div style="background:#2a2a3e;border-radius:8px;padding:12px;font-size:12px;line-height:1.6;">
                <div style="font-size:14px;font-weight:bold;margin-bottom:8px;color:#a78bfa;">📌 ${bp.work_title || '未命名'}</div>
                <div style="margin-bottom:6px;"><b>Why Good:</b><br>${bp.why_good || ''}</div>
                <div style="margin-bottom:6px;"><b>Plain Text:</b><br><code style="font-size:11px;color:#94a3b8;">${bp.plain_text || ''}</code></div>
                <div style="margin-bottom:6px;"><b>Optimized Prompt:</b><br><code style="font-size:11px;color:#94a3b8;">${bp.output_prompt_plain || ''}</code></div>
                <div style="margin-bottom:6px;"><b>Type:</b> ${bp.image_type || ''} &nbsp; <b>Vibe:</b> ${bp.vibe || ''}</div>
                <div><b>Tags:</b> ${(bp.tags || []).map(t => '<span style="background:#374151;padding:2px 6px;border-radius:4px;margin:2px;display:inline-block;">' + t + '</span>').join('')}</div>
            </div>
        `;
    }

    async runAnalysis() {
        const imageUrl = this.blobs[this.currentIndex];
        const userInputEl = document.getElementById('aestheticUserInput');
        const userInput = userInputEl ? userInputEl.value.trim() : '';
        const submitBtn = document.getElementById('aestheticSubmitBtn');
        const inputArea = document.getElementById('aestheticInputArea');
        const resultDiv = document.getElementById('aestheticResult');
        const force = submitBtn.dataset.force === 'true';

        submitBtn.disabled = true;
        submitBtn.textContent = '⏳ 提交中...';
        this.isAnalyzing = true;
        this.renderCurrentImage();

        try {
            const response = await fetch(`${this.apiBase}/api/aesthetic/analyze`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'ngrok-skip-browser-warning': '1'
                },
                body: JSON.stringify({
                    image_url: imageUrl,
                    user_why_good: userInput,
                    force: force,
                    favorite_id: window._activeFavoriteId || ''
                })
            });
            const data = await response.json();

            if (data.status === 'cached' && data.blueprint) {
                // 返回缓存结果
                inputArea.style.display = 'none';
                this._renderBlueprint(data.blueprint, resultDiv);
                submitBtn.textContent = '🔄 重新分析';
                submitBtn.style.background = '#4b5563';
                submitBtn.dataset.force = 'true';
                submitBtn.disabled = false;
            } else if (data.status === 'submitted' && data.task_id) {
                // 异步提交成功，开始轮询
                inputArea.style.display = 'none';
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = `<div style="text-align:center;padding:16px;color:#a78bfa;">
                    <div style="font-size:20px;margin-bottom:8px;">⏳</div>
                    <div>美学分析进行中...</div>
                    <div id="aestheticPollStatus" style="font-size:11px;color:#666;margin-top:4px;">GPT-4o 正在分析图片</div>
                </div>`;
                submitBtn.style.display = 'none';
                this._pollAestheticTask(data.task_id, resultDiv, submitBtn);
            } else {
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = `<div style="color:#ef4444;">❌ ${data.message || '提交失败'}</div>`;
                submitBtn.textContent = '重试';
                submitBtn.disabled = false;
            }
        } catch (error) {
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = `<div style="color:#ef4444;">❌ 网络错误: ${error.message}</div>`;
            submitBtn.textContent = '重试';
            submitBtn.disabled = false;
        } finally {
            this.isAnalyzing = false;
            this.renderCurrentImage();
        }
    }

    async _pollAestheticTask(taskId, resultDiv, submitBtn) {
        const maxAttempts = 120; // 最多轮询 2 分钟
        for (let i = 0; i < maxAttempts; i++) {
            await new Promise(r => setTimeout(r, 2000));

            // 弹窗已关闭则停止轮询
            if (!document.getElementById('aestheticModal')) return;

            try {
                const res = await fetch(`${this.apiBase}/api/aesthetic/status?task_id=${taskId}`, {
                    headers: { 'ngrok-skip-browser-warning': '1' }
                });
                const data = await res.json();

                const statusEl = document.getElementById('aestheticPollStatus');
                if (statusEl) {
                    const elapsed = Math.round((i + 1) * 2);
                    statusEl.textContent = `已等待 ${elapsed}s...`;
                }

                if (data.status === 'success' && data.blueprint) {
                    this._renderBlueprint(data.blueprint, resultDiv);
                    submitBtn.style.display = 'block';
                    submitBtn.textContent = '🔄 重新分析';
                    submitBtn.style.background = '#4b5563';
                    submitBtn.dataset.force = 'true';
                    submitBtn.disabled = false;
                    return;
                } else if (data.status === 'error') {
                    resultDiv.innerHTML = `<div style="color:#ef4444;">❌ ${data.message || '分析失败'}</div>`;
                    submitBtn.style.display = 'block';
                    submitBtn.textContent = '重试';
                    submitBtn.disabled = false;
                    return;
                }
                // status === 'running' → 继续轮询
            } catch {
                // 网络错误，继续轮询
            }
        }
        // 超时
        resultDiv.innerHTML = `<div style="color:#f59e0b;">⚠️ 分析超时（>2分钟），请稍后再查看</div>`;
        submitBtn.style.display = 'block';
        submitBtn.textContent = '重试';
        submitBtn.disabled = false;
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    window.gallery = new Gallery(document.body.dataset.apiBase || 'http://localhost:53133');
});
