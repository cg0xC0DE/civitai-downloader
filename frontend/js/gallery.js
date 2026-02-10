/**
 * Azure Blob Gallery - 图片画廊
 * 查询并展示 Azure Blob 中的绘图结果
 */

class Gallery {
    constructor(apiBase) {
        this.apiBase = apiBase;
        this.blobs = [];
        this.currentIndex = 0;
        this.pageSize = 20;
        this.isLoading = false;
        this.isDeleting = false;
        this.isAnalyzing = false;
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
            const response = await fetch(`${this.apiBase}/api/azure/list`);
            const data = await response.json();

            if (data.success && data.blobs) {
                // 按时间倒序（最新的在前）
                this.blobs = data.blobs;
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

        this.els.imageContainer.innerHTML = `
            <img src="${url}" alt="${filename}" class="gallery-image" onload="gallery.onImageLoad()"
                 onerror="gallery.onImageError()">
        `;

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
            <div class="gallery-info-item">
                <span class="gallery-info-label">URL</span>
                <span class="gallery-info-value gallery-url">${url}</span>
            </div>
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
                alert('已删除');
            } else {
                alert('删除失败: ' + result.error);
            }
        } catch (error) {
            console.error('Delete error:', error);
            alert('删除失败: ' + error.message);
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

    onImageError() {
        this.els.imageContainer.innerHTML = `
            <div class="gallery-error">
                <p>❌ 图片加载失败</p>
                <p class="gallery-error-url">${this.blobs[this.currentIndex]}</p>
            </div>
        `;
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
