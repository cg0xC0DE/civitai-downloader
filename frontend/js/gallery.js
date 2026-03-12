/**
 * Azure Blob Gallery - 图片画廊
 * 查询并展示 Azure Blob 中的绘图结果
 */

class Gallery {
    constructor(apiBase) {
        this.apiBase = apiBase;
        this.blobs = [];        // 当前筛选后的 blob 列表
        this._allBlobs = [];    // 全量 blob 列表（筛选源）
        this.tracking = {};     // batch_id -> { favorite_id, source_url, status, image_statuses }
        this.currentIndex = 0;
        this.pageSize = 20;
        this.isLoading = false;
        this._deletingPaths = new Set();
        this.isAnalyzing = false;
        this.compareMode = true;
        this._compareCache = {};
        this._deleteHoldTimer = null;
        this._deleteHoldProgressTimer = null;
        this._deleteHoldStartAt = 0;
        this._deleteHoldBtn = null;
        this._deleteHoldDurationMs = 3000;
        this._deleteHoldTriggered = false;
        this._deleteHoldConsumeTimer = null;
        this._bulkDeleting = false;
        this.filter = 'pending_review'; // 默认显示复刻待评估
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
        this.els.refreshBtn.addEventListener('click', () => this.loadBlobs(true));
        this.els.prevBtn.addEventListener('click', () => this.prevImage());
        this.els.nextBtn.addEventListener('click', () => this.nextImage());

        // 筛选器
        const filterSel = document.getElementById('galleryFilterSelect');
        if (filterSel) {
            filterSel.value = this.filter;
            filterSel.addEventListener('change', () => {
                this.filter = filterSel.value;
                this._applyFilter();
            });
        }

        // 切到作品 tab 时才首次加载（不阻塞页面启动）
        this._loaded = false;
        const tabBtn = document.querySelector('.tab-btn[data-tab="gallery"]');
        if (tabBtn) {
            tabBtn.addEventListener('click', () => {
                if (!this._loaded) { this._loaded = true; this.loadBlobs(); }
            });
        }
    }

    _imageKeyFromUrl(url) {
        return this.extractFilename(url).split('?')[0].split('#')[0];
    }

    _getImageStatus(url, batchId = '') {
        const key = this._imageKeyFromUrl(url);
        const bid = batchId || (key.split('_')[0] || '');
        const info = this.tracking[bid];
        if (!info) return 'pending_review';
        const imageStatuses = info.image_statuses || {};
        if (key && imageStatuses[key]) return imageStatuses[key];
        return info.status || 'pending_review';
    }

    _setImageStatusLocal(batchId, imageKey, status) {
        if (!batchId || !this.tracking[batchId]) return;
        if (!imageKey) {
            this.tracking[batchId].status = status;
            return;
        }
        if (!this.tracking[batchId].image_statuses) this.tracking[batchId].image_statuses = {};
        this.tracking[batchId].image_statuses[imageKey] = status;
    }

    _setDeleteHoldVisual(btn, progress = 0) {
        if (!btn) return;
        if (btn.dataset.holdUiReady !== '1') {
            btn.dataset.holdUiReady = '1';
            btn.dataset.holdDefaultLabel = '🗑️ 删除';
            btn.style.display = 'flex';
            btn.style.alignItems = 'center';
            btn.style.justifyContent = 'center';
            btn.style.gap = '6px';
            btn.innerHTML = `
                <span class="delete-hold-ring" aria-hidden="true" style="width:14px;height:14px;border-radius:999px;border:2px solid rgba(220,38,38,0.25);background:conic-gradient(#dc2626 0deg, rgba(220,38,38,0.18) 0deg);flex:0 0 14px;"></span>
                <span class="delete-hold-label">🗑️ 删除</span>
            `;
        }

        const pct = Math.max(0, Math.min(1, progress));
        const deg = Math.round(360 * pct);
        const ring = btn.querySelector('.delete-hold-ring');
        if (ring) {
            ring.style.background = `conic-gradient(#dc2626 ${deg}deg, rgba(220,38,38,0.18) ${deg}deg)`;
        }
        const label = btn.querySelector('.delete-hold-label');
        if (!label) return;
        if (pct > 0 && pct < 1) {
            const remainSec = Math.max(0, (this._deleteHoldDurationMs * (1 - pct)) / 1000);
            label.textContent = `🗑️ 删除 (${remainSec.toFixed(1)}s)`;
        } else {
            label.textContent = btn.dataset.holdDefaultLabel || '🗑️ 删除';
        }
    }

    _bindDeleteLongPress(batchId, favId) {
        const btn = document.getElementById('galleryDeleteBtn');
        if (!btn) return;

        btn.title = '点击删除当前图；长按 3 秒：删除该批变体并标记失败';
        this._setDeleteHoldVisual(btn, 0);

        btn.addEventListener('click', (e) => {
            if (!this._deleteHoldTriggered) return;
            this._deleteHoldTriggered = false;
            if (this._deleteHoldConsumeTimer) {
                clearTimeout(this._deleteHoldConsumeTimer);
                this._deleteHoldConsumeTimer = null;
            }
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
        }, true);

        const startHold = (e) => {
            if (this._bulkDeleting) return;
            if (e.type === 'mousedown' && e.button !== 0) return;
            if (this._deleteHoldTimer) return;
            this._clearDeleteHold();
            this._deleteHoldTriggered = false;
            this._deleteHoldBtn = btn;
            this._deleteHoldStartAt = Date.now();
            this._setDeleteHoldVisual(btn, 0);
            this._deleteHoldProgressTimer = setInterval(() => {
                if (!this._deleteHoldBtn) return;
                const elapsed = Date.now() - this._deleteHoldStartAt;
                this._setDeleteHoldVisual(this._deleteHoldBtn, elapsed / this._deleteHoldDurationMs);
            }, 50);
            this._deleteHoldTimer = setTimeout(() => {
                this._deleteHoldTimer = null;
                this._clearDeleteHold();
                this._deleteHoldTriggered = true;
                if (this._deleteHoldConsumeTimer) clearTimeout(this._deleteHoldConsumeTimer);
                this._deleteHoldConsumeTimer = setTimeout(() => {
                    this._deleteHoldTriggered = false;
                    this._deleteHoldConsumeTimer = null;
                }, 350);
                this._deleteBatchVariantsAndFail(batchId, favId).catch(err => {
                    showToast('批量删除失败: ' + err.message, 'error');
                });
            }, this._deleteHoldDurationMs);
        };

        const cancelHold = () => this._clearDeleteHold();

        btn.addEventListener('mousedown', startHold);
        btn.addEventListener('touchstart', startHold, { passive: true });
        btn.addEventListener('mouseup', cancelHold);
        btn.addEventListener('mouseleave', cancelHold);
        btn.addEventListener('touchend', cancelHold);
        btn.addEventListener('touchcancel', cancelHold);
    }

    _clearDeleteHold() {
        if (this._deleteHoldTimer) {
            clearTimeout(this._deleteHoldTimer);
            this._deleteHoldTimer = null;
        }
        if (this._deleteHoldProgressTimer) {
            clearInterval(this._deleteHoldProgressTimer);
            this._deleteHoldProgressTimer = null;
        }
        if (this._deleteHoldBtn) {
            this._setDeleteHoldVisual(this._deleteHoldBtn, 0);
            this._deleteHoldBtn = null;
        }
    }

    async _deleteBlobRemote(path, timeoutMs = 12000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(`${this.apiBase}/api/azure/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                body: JSON.stringify({ path }),
                signal: controller.signal,
            });

            let result = null;
            try {
                result = await response.json();
            } catch {
                result = null;
            }

            if (!response.ok) {
                return { success: false, error: (result && result.error) || `HTTP ${response.status}` };
            }
            return { success: !!(result && result.success), error: (result && result.error) || '' };
        } catch (error) {
            if (error && error.name === 'AbortError') {
                return { success: false, error: '删除请求超时' };
            }
            throw error;
        } finally {
            clearTimeout(timer);
        }
    }

    async _deleteBatchVariantsAndFail(batchId, favId) {
        if (!batchId || this._bulkDeleting) return;
        this._bulkDeleting = true;
        this._clearDeleteHold();

        try {
            // 长按后直接进入“标记失败”子面板
            const failReason = await this._pickFailReason();

            const tracked = (this.tracking[batchId] && this.tracking[batchId].blob_urls) || [];
            const fromAll = this._allBlobs.filter(u => (this.extractFilename(u).split('_')[0] || '') === batchId);
            const batchUrls = Array.from(new Set([...(tracked || []), ...fromAll]));

            const deletePaths = [];
            const seenPaths = new Set();
            for (const u of batchUrls) {
                const p = this.extractBlobPath(u);
                if (!p || seenPaths.has(p)) continue;
                seenPaths.add(p);
                deletePaths.push(p);
            }

            if (batchUrls.length === 0) {
                showToast('该批次暂无可删除图片', 'error');
            } else {
                const urlSet = new Set(batchUrls);
                this._allBlobs = this._allBlobs.filter(u => !urlSet.has(u));
                this.blobs = this.blobs.filter(u => !urlSet.has(u));

                if (this.tracking[batchId]) {
                    this.tracking[batchId].blob_urls = (this.tracking[batchId].blob_urls || []).filter(u => !urlSet.has(u));
                    if (this.tracking[batchId].image_statuses) {
                        batchUrls.forEach(u => {
                            const k = this._imageKeyFromUrl(u);
                            if (k) delete this.tracking[batchId].image_statuses[k];
                        });
                    }
                }

                if (this.currentIndex >= this.blobs.length) {
                    this.currentIndex = Math.max(0, this.blobs.length - 1);
                }
                this.render();

                // 后台异步删除（不阻塞 UI）
                deletePaths.forEach((path) => {
                    if (this._deletingPaths.has(path)) return;
                    this._deletingPaths.add(path);
                    this._deleteBlobRemote(path)
                        .catch(() => {})
                        .finally(() => this._deletingPaths.delete(path));
                });

                // 后台异步标记失败（不阻塞 UI）
                if (favId) {
                    fetch(`${this.apiBase}/api/favorite/update-status`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                        body: JSON.stringify({ id: favId, status: 'fail', fail_reason: failReason })
                    }).catch(() => {});
                }

                showToast(`已提交后台删除 ${deletePaths.length} 张，并标记为失败`);
            }
        } finally {
            this._bulkDeleting = false;
        }
    }

    async loadBlobs(forceRefresh = false) {
        if (this.isLoading) return;

        this.isLoading = true;
        this.showLoading(true);
        this.els.emptyState.style.display = 'none';
        this.els.imageContainer.innerHTML = '';
        if (this._bgPreloadTimer) { clearTimeout(this._bgPreloadTimer); this._bgPreloadTimer = null; }

        try {
            // 纯本地读 tracking，秒返回
            const response = await fetch(`${this.apiBase}/api/gen-tracking/list`, {
                headers: { 'ngrok-skip-browser-warning': '1' }
            });
            const data = await response.json();

            if (data.status === 'ok' && data.tracking) {
                this.tracking = data.tracking;
                // 从 tracking 的 blob_urls 构建全量 blob 列表（按 created_at 倒序）
                const entries = Object.entries(this.tracking)
                    .filter(([, v]) => v.blob_urls && v.blob_urls.length > 0)
                    .sort((a, b) => (b[1].created_at || '').localeCompare(a[1].created_at || ''));
                this._allBlobs = [];
                for (const [, v] of entries) {
                    for (const url of v.blob_urls) {
                        this._allBlobs.push(url);
                    }
                }
                this._applyFilter();
                this._startBgPreload();
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

    _applyFilter() {
        if (this.filter === 'all') {
            this.blobs = this._allBlobs.slice();
        } else {
            this.blobs = this._allBlobs.filter(url => {
                const filename = this.extractFilename(url);
                const batchId = filename.split('_')[0] || '';
                const st = this._getImageStatus(url, batchId);
                return st === this.filter;
            });
        }
        this.currentIndex = 0;
        this.render();
        this._preloadAround();
    }

    // 重新筛选但保持 currentIndex（标记后跳页用，不重置到 0）
    _applyFilterKeepIndex() {
        const savedIndex = this.currentIndex;
        if (this.filter === 'all') {
            this.blobs = this._allBlobs.slice();
        } else {
            this.blobs = this._allBlobs.filter(url => {
                const filename = this.extractFilename(url);
                const batchId = filename.split('_')[0] || '';
                const st = this._getImageStatus(url, batchId);
                return st === this.filter;
            });
        }
        // 钳制 index 到合法范围
        this.currentIndex = Math.min(savedIndex, Math.max(0, this.blobs.length - 1));
        this.updateCounter();
        this.updateButtons();
        this._preloadAround();
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
        const imageKey = this._imageKeyFromUrl(url);
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
        // 当前 batch 的 status
        const currentStatus = this._getImageStatus(url, batchId);
        const statusLabel = { pending_review: '⏳ 待评估', perfect: '⭐ 完美复刻', done: '✅ 已完成' }[currentStatus] || '';

        const trackingRow = favId ? `
            <div class="gallery-info-item" style="border: none; padding-top: 8px; display: flex; gap: 8px; flex-wrap: wrap;">
                ${compareBtn}
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #8b5cf6; border-color: #8b5cf6; min-width: 0;"
                        onclick="gallery.inheritToDraw('${batchId}')">
                    🧬 继承
                </button>
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #2563eb; border-color: #2563eb; min-width: 0;"
                        onclick="gallery.goToFavorite('${favId}')">
                    ⭐ 查看来源收藏
                </button>
            </div>` : '';

        const statusBadge = `<div class="gallery-info-item"><span class="gallery-info-label">复刻状态</span><span class="gallery-info-value">${statusLabel}</span></div>`;

        const actionBtns = batchId ? `
            <div class="gallery-info-item" style="border:none;padding-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
                <button class="btn btn-outline btn-sm" style="flex:1;color:#f59e0b;border-color:#f59e0b;min-width:0;"
                        onclick="gallery.markPerfect('${batchId}','${imageKey}')">
                    ⭐ 完美复刻
                </button>
                <button class="btn btn-outline btn-sm" style="flex:1;color:#22c55e;border-color:#22c55e;min-width:0;"
                        onclick="gallery.markDone('${batchId}','${imageKey}')">
                    ✅ 置为完成
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
            ${statusBadge}
            ${favSourceUrl ? `
                <div class="gallery-info-item">
                    <span class="gallery-info-label">来源</span>
                    <span class="gallery-info-value"><a href="${favSourceUrl}" target="_blank" style="color:var(--accent);">${favSourceUrl.length > 50 ? favSourceUrl.slice(0, 50) + '...' : favSourceUrl}</a></span>
                </div>
            ` : ''}
            <div class="gallery-info-item" style="border: none; padding-top: 12px; display: flex; gap: 8px;">
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #22c55e; border-color: #22c55e;"
                        onclick="gallery.downloadImage()">
                    ⬇️ 下载
                </button>
                ${blobPath ? `<button class="btn btn-outline btn-sm" style="flex: 1; color: #dc2626; border-color: #dc2626;"
                        id="galleryDeleteBtn" onclick="gallery.deleteBlob('${blobPath}')">
                    🗑️ 删除
                </button>` : ''}
                <button class="btn btn-outline btn-sm" style="flex: 1; color: #7c3aed; border-color: #7c3aed;"
                        onclick="gallery.showAnalyzeModal()" ${this.isAnalyzing ? 'disabled' : ''}>
                    ${this.isAnalyzing ? '⏳ 分析中...' : '🔍 美学分析'}
                </button>
            </div>
            ${actionBtns}
            ${trackingRow}
        `;

        this._bindDeleteLongPress(batchId, favId);
    }

    _sendStatusUpdate(batchId, status, imageKey = '') {
        // fire-and-forget，不阻塞 UI
        fetch(`${this.apiBase}/api/gen-tracking/update-status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
            body: JSON.stringify({ batch_id: batchId, status, image_key: imageKey })
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') showToast('更新失败: ' + (data.message || ''), 'error');
        })
        .catch(e => showToast('网络错误: ' + e.message, 'error'));
    }

    markPerfect(batchId, imageKey = '') {
        // 1. 立即更新本地缓存
        this._setImageStatusLocal(batchId, imageKey, 'perfect');
        // 2. 立即跳下一张（在筛选前，index 还有效）
        this._autoAdvance();
        // 3. 重新筛选（当前图已不在 pending_review 列表，但 index 已指向下一张）
        this._applyFilterKeepIndex();
        showToast('⭐ 已标记为完美复刻');
        // 4. 异步发送到后端
        this._sendStatusUpdate(batchId, 'perfect', imageKey);
    }

    markDone(batchId, imageKey = '') {
        this._setImageStatusLocal(batchId, imageKey, 'done');
        this._autoAdvance();
        this._applyFilterKeepIndex();
        showToast('✅ 已置为完成');
        this._sendStatusUpdate(batchId, 'done', imageKey);
    }

    _autoAdvance() {
        // 标记后自动跳下一张；若已是最后一张则跳上一张
        if (this.blobs.length === 0) return;
        if (this.currentIndex < this.blobs.length - 1) {
            this.currentIndex++;
        } else if (this.currentIndex > 0) {
            this.currentIndex--;
        }
        this.renderCurrentImage();
        this.updateCounter();
        this.updateButtons();
        this._preloadAround();
    }

    async downloadImage() {
        if (this.currentIndex < 0 || this.currentIndex >= this.blobs.length) return;
        const url = this.blobs[this.currentIndex];
        const filename = this.extractFilename(url);
        try {
            const resp = await fetch(url);
            const blob = await resp.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
            if (typeof showToast === 'function') showToast('下载已开始');
        } catch (err) {
            console.error('Download error:', err);
            if (typeof showToast === 'function') showToast('下载失败: ' + err.message, 'error');
        }
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
            if (!url) return '';
            const clean = String(url).split('#')[0];

            // 绝对 URL：优先使用 URL 解析，自动去掉 query
            if (/^https?:\/\//i.test(clean)) {
                const u = new URL(clean);
                const parts = u.pathname.replace(/^\/+/, '').split('/');
                if (parts.length >= 2) {
                    // parts[0] = container（如 civitaidl）
                    return decodeURIComponent(parts.slice(1).join('/'));
                }
                return decodeURIComponent(u.pathname.replace(/^\/+/, ''));
            }

            // 兜底：相对路径/异常字符串
            const noQuery = clean.split('?')[0].replace(/^\/+/, '');
            const parts = noQuery.split('/');
            if (parts.length >= 2 && parts[0] === 'civitaidl') {
                return decodeURIComponent(parts.slice(1).join('/'));
            }
            return decodeURIComponent(noQuery);
        } catch {
            return '';
        }
    }

    async deleteBlob(blobPath) {
        this._clearDeleteHold();
        if (this._bulkDeleting) return;

        const url = this.blobs[this.currentIndex];
        const filename = this.extractFilename(url);
        const batchId = filename.split('_')[0] || '';
        const trackInfo = this.tracking[batchId] || null;
        const favId = trackInfo ? trackInfo.favorite_id : '';

        // 计算同一原图下还有几张生成图
        let siblingCount = 0;
        if (favId) {
            for (const blobUrl of this.blobs) {
                const bid = this.extractFilename(blobUrl).split('_')[0] || '';
                const ti = this.tracking[bid] || null;
                if (ti && ti.favorite_id === favId) siblingCount++;
            }
        }

        // 最后一张：弹框询问如何处理原图
        let favAction = null; // null=取消, 'fail', 'pending', 'delete-only', 'regenerate'
        if (favId && siblingCount === 1) {
            const trackInfoForModal = this.tracking[batchId] || null;
            const sourceUrlForModal = trackInfoForModal ? trackInfoForModal.source_url : '';
            favAction = await this._confirmDeleteLast(sourceUrlForModal);
            if (favAction === null) return; // 取消
        }

        // 先收集失败原因，避免等待远端删除请求后才弹出二级弹窗（体感卡顿）
        let failReason = '';
        if (favAction === 'fail' && favId) {
            failReason = await this._pickFailReason();
        }

        // 乐观更新：立即从所有内存列表中移除并渲染下一张
        const deletedUrl = this.blobs[this.currentIndex];
        const deletedKey = this._imageKeyFromUrl(deletedUrl);
        this.blobs.splice(this.currentIndex, 1);
        // 同步移除 _allBlobs（防止 _applyFilter 重建时恢复）
        const allIdx = this._allBlobs.indexOf(deletedUrl);
        if (allIdx !== -1) this._allBlobs.splice(allIdx, 1);
        // 同步移除 tracking 中的 blob_urls
        if (batchId && this.tracking[batchId] && this.tracking[batchId].blob_urls) {
            this.tracking[batchId].blob_urls = this.tracking[batchId].blob_urls.filter(u => u !== deletedUrl);
            if (this.tracking[batchId].image_statuses && deletedKey) {
                delete this.tracking[batchId].image_statuses[deletedKey];
            }
        }
        if (this.currentIndex >= this.blobs.length) {
            this.currentIndex = Math.max(0, this.blobs.length - 1);
        }
        this.render();

        // 防止同一张图重复删除（乐观更新后图已移除，此处仅作保险）
        if (this._deletingPaths.has(blobPath)) return;
        this._deletingPaths.add(blobPath);

        // 后台异步删除 blob（不阻塞 UI）
        this._deleteBlobRemote(blobPath)
            .catch(() => {})
            .finally(() => this._deletingPaths.delete(blobPath));
        showToast('已删除（后台执行）');

        // 写入废弃记录（非标记失败、非重新生图、非参数调整重试时记录参数指纹）
        if (favId && favAction !== 'fail' && favAction !== 'regenerate' && favAction !== 'retry') {
            const genParams = trackInfo ? (trackInfo.gen_params || null) : null;
            if (genParams && genParams.checkpoint) {
                fetch(`${this.apiBase}/api/discard-log/add`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                    body: JSON.stringify({ fav_id: favId, params: genParams })
                }).catch(() => {});
            }
        }

        // 按用户选择标记原图状态 / 重新生图
        if (favAction === 'regenerate') {
            // 切换到生图 tab，填入 source_url，触发解析
            const trackInfoRegen = this.tracking[batchId] || null;
            const regenUrl = trackInfoRegen ? trackInfoRegen.source_url : '';
            if (regenUrl) {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                document.querySelector('.tab-btn[data-tab="draw"]').classList.add('active');
                document.getElementById('draw').classList.add('active');
                document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.draw-mode').forEach(m => m.classList.remove('active'));
                document.querySelector('.mode-btn[data-mode="auto"]').classList.add('active');
                document.getElementById('autoMode').classList.add('active');
                const urlInput = document.getElementById('imageUrl');
                if (urlInput) { urlInput.value = regenUrl; document.getElementById('parseBtn')?.click(); }
            }
        } else if (favAction === 'fail' && favId) {
            fetch(`${this.apiBase}/api/favorite/update-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                body: JSON.stringify({ id: favId, status: 'fail', fail_reason: failReason })
            }).catch(() => {});
            showToast('已标记原图为失败（后台执行）');
        } else if (favAction === 'retry' && favId) {
            // 参数需调整：标记为 pending + retry_reason，自动复刻会重新处理
            fetch(`${this.apiBase}/api/favorite/update-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                body: JSON.stringify({ id: favId, status: 'pending', retry_reason: '参数需调整，重新生成' })
            }).catch(() => {});
            showToast('已标记为待调整，将重新生成（后台执行）');
        } else if (favAction === 'pending' && favId) {
            fetch(`${this.apiBase}/api/favorite/update-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                body: JSON.stringify({ id: favId, status: 'pending' })
            }).catch(() => {});
            showToast('已标记原图为未处理（后台执行）');
        }
    }

    _pickFailReason() {
        return new Promise(resolve => {
            const old = document.getElementById('failReasonModal');
            if (old) old.remove();

            const modal = document.createElement('div');
            modal.id = 'failReasonModal';
            modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000;';
            modal.innerHTML = `
                <div style="background:#1e1e2e;border-radius:12px;padding:24px;width:90%;max-width:420px;color:#e0e0e0;">
                    <div style="font-size:15px;font-weight:600;margin-bottom:6px;">❌ 失败原因</div>
                    <div style="font-size:12px;color:#9ca3af;margin-bottom:14px;">选择一个原因，方便后续排查</div>
                    <div style="display:flex;flex-direction:column;gap:7px;" id="failReasonBtns">
                        <button class="fail-reason-btn btn btn-outline btn-sm" data-reason="权重穷举步进无满意结果" style="text-align:left;padding:9px 12px;">📊 权重穷举步进无满意结果</button>
                        <button class="fail-reason-btn btn btn-outline btn-sm" data-reason="模型找不到或已下架" style="text-align:left;padding:9px 12px;">🔍 模型找不到或已下架</button>
                        <button class="fail-reason-btn btn btn-outline btn-sm" data-reason="生成质量太差，不值得保留" style="text-align:left;padding:9px 12px;">🎨 生成质量太差，不值得保留</button>
                        <button class="fail-reason-btn btn btn-outline btn-sm" data-reason="提示词解析失败或参数缺失" style="text-align:left;padding:9px 12px;">📝 提示词解析失败或参数缺失</button>
                        <button class="fail-reason-btn btn btn-outline btn-sm" data-reason="原图风格无法复刻" style="text-align:left;padding:9px 12px;">🖼️ 原图风格无法复刻</button>
                        <button class="fail-reason-btn btn btn-outline btn-sm" data-reason="ComfyUI 工作流报错" style="text-align:left;padding:9px 12px;">⚙️ ComfyUI 工作流报错</button>
                        <div style="display:flex;gap:8px;align-items:center;margin-top:2px;">
                            <input id="failReasonCustom" type="text" placeholder="其他原因（自定义）..."
                                style="flex:1;padding:8px 10px;border:1px solid #444;border-radius:8px;background:#2a2a3e;color:#e0e0e0;font-size:13px;">
                            <button id="failReasonCustomBtn" class="btn btn-sm" style="background:#6b7280;color:#fff;border:none;padding:8px 12px;white-space:nowrap;">确认</button>
                        </div>
                    </div>
                    <button id="failReasonSkip" class="btn btn-outline btn-sm" style="width:100%;margin-top:12px;padding:8px;">跳过（不记录原因）</button>
                </div>`;
            document.body.appendChild(modal);

            const cleanup = () => modal.remove();

            // 预设选项
            modal.querySelectorAll('.fail-reason-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    cleanup();
                    resolve(btn.dataset.reason);
                });
            });

            // 自定义输入
            document.getElementById('failReasonCustomBtn').addEventListener('click', () => {
                const val = document.getElementById('failReasonCustom').value.trim();
                cleanup();
                resolve(val || '');
            });
            document.getElementById('failReasonCustom').addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    const val = e.target.value.trim();
                    cleanup();
                    resolve(val || '');
                }
            });

            // 跳过
            document.getElementById('failReasonSkip').addEventListener('click', () => { cleanup(); resolve(''); });
            modal.addEventListener('click', e => { if (e.target === modal) { cleanup(); resolve(''); } });
        });
    }

    _confirmDeleteLast(sourceUrl) {
        return new Promise(resolve => {
            const old = document.getElementById('deleteConfirmModal');
            if (old) old.remove();

            const modal = document.createElement('div');
            modal.id = 'deleteConfirmModal';
            modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:9999;';
            const regenBtn = sourceUrl ? `<button id="dlOptRegen" class="btn btn-sm" style="background:#7c3aed;color:#fff;border:none;padding:10px;">🎨 删除并重新生图</button>` : '';
            modal.innerHTML = `
                <div style="background:#1e1e2e;border-radius:12px;padding:24px;width:90%;max-width:380px;color:#e0e0e0;">
                    <div style="font-size:15px;font-weight:600;margin-bottom:6px;">🗑️ 这是最后一张生成图</div>
                    <div style="font-size:12px;color:#9ca3af;margin-bottom:16px;">删除后如何处理来源原图？</div>
                    <div style="display:flex;flex-direction:column;gap:8px;">
                        <button id="dlOptFail" class="btn btn-sm" style="background:#dc2626;color:#fff;border:none;padding:10px;">❌ 删除并标记原图为失败</button>
                        <button id="dlOptRetry" class="btn btn-sm" style="background:#0891b2;color:#fff;border:none;padding:10px;">🔧 参数需调整，重新生成</button>
                        <button id="dlOptPending" class="btn btn-sm" style="background:#f59e0b;color:#fff;border:none;padding:10px;">🔄 删除并标记原图为未处理</button>
                        ${regenBtn}
                        <button id="dlOptOnly" class="btn btn-outline btn-sm" style="padding:10px;">🗑️ 仅删除图片</button>
                        <button id="dlOptCancel" class="btn btn-outline btn-sm" style="padding:10px;">取消</button>
                    </div>
                </div>`;
            document.body.appendChild(modal);

            const cleanup = () => modal.remove();
            document.getElementById('dlOptFail').addEventListener('click',    () => { cleanup(); resolve('fail'); });
            document.getElementById('dlOptRetry').addEventListener('click',   () => { cleanup(); resolve('retry'); });
            document.getElementById('dlOptPending').addEventListener('click', () => { cleanup(); resolve('pending'); });
            if (sourceUrl) document.getElementById('dlOptRegen').addEventListener('click', () => { cleanup(); resolve('regenerate'); });
            document.getElementById('dlOptOnly').addEventListener('click',    () => { cleanup(); resolve('delete-only'); });
            document.getElementById('dlOptCancel').addEventListener('click',  () => { cleanup(); resolve(null); });
            modal.addEventListener('click', e => { if (e.target === modal) { cleanup(); resolve(null); } });
        });
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
            this._preloadAround();
        }
    }

    nextImage() {
        if (this.currentIndex < this.blobs.length - 1) {
            this.currentIndex++;
            this.renderCurrentImage();
            this.updateCounter();
            this.updateButtons();
            this._preloadAround();
        }
    }

    onImageLoad() {
        // 图片加载成功
    }

    _preloadAround(radius = 5) {
        // 仅预加载生成图片本身（前后 radius 张），compareCache 由 _startBgPreload 统一处理
        if (!this._preloadCache) this._preloadCache = new Set();
        const start = Math.max(0, this.currentIndex - radius);
        const end = Math.min(this.blobs.length - 1, this.currentIndex + radius);
        for (let i = start; i <= end; i++) {
            if (i === this.currentIndex) continue;
            const url = this.blobs[i];
            if (!url || this._preloadCache.has(url)) continue;
            this._preloadCache.add(url);
            const img = new Image();
            img.src = url;
        }
    }

    // 后台无限预加载：从当前位置向后逐张预取 compareCache
    _startBgPreload() {
        if (this._bgPreloadTimer) clearTimeout(this._bgPreloadTimer);
        this._bgPreloadIdx = this.currentIndex;
        this._bgPreloadStep();
    }

    _bgPreloadStep() {
        if (!this.blobs.length) return;
        // 找下一个未缓存的
        let idx = (this._bgPreloadIdx === undefined) ? this.currentIndex : this._bgPreloadIdx;
        // 向后扫描
        let found = false;
        for (let i = idx; i < this.blobs.length; i++) {
            const url = this.blobs[i];
            if (!url) continue;
            const filename = this.extractFilename(url);
            const batchId = filename.split('_')[0] || '';
            const trackInfo = this.tracking[batchId];
            const sourceUrl = trackInfo ? trackInfo.source_url : '';
            if (!sourceUrl) continue;
            const m = sourceUrl.match(/\/images\/(\d+)/);
            if (!m) continue;
            const imageId = m[1];
            if (this._compareCache[imageId]) continue; // 已缓存，跳过
            // 预取这一张
            this._bgPreloadIdx = i + 1;
            fetch(`${this.apiBase}/api/image/thumb?id=${imageId}`, {
                headers: { 'ngrok-skip-browser-warning': '1' }
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok') {
                    this._compareCache[imageId] = data;
                    if (data.thumb_url) {
                        const img = new Image();
                        img.src = data.thumb_url.replace(/\/width=\d+/, '/width=1024');
                    }
                }
                // 继续下一张（间隔 200ms 避免请求风暴）
                this._bgPreloadTimer = setTimeout(() => this._bgPreloadStep(), 200);
            })
            .catch(() => {
                this._bgPreloadTimer = setTimeout(() => this._bgPreloadStep(), 500);
            });
            found = true;
            break;
        }
        // 全部预取完毕，不再调度
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

    inheritToDraw(batchId) {
        if (!batchId) return;
        const info = this.tracking[batchId] || null;
        if (!info) {
            showToast('未找到该作品的追踪记录', 'error');
            return;
        }

        const genParams = info.gen_params || {};
        const hasInheritable = !!(genParams.checkpoint || genParams.prompt);
        const sourceUrl = info.source_url || '';
        const favoriteId = info.favorite_id || '';

        if (window.app && typeof window.app.inheritFromGallery === 'function' && hasInheritable) {
            window.app.inheritFromGallery(genParams, sourceUrl, favoriteId);
            return;
        }

        // 兼容旧 tracking：若无 gen_params，降级为按来源 URL 解析
        if (window.app && typeof window.app.goToDrawWithUrl === 'function' && sourceUrl) {
            window.app.goToDrawWithUrl(sourceUrl, favoriteId);
            showToast('该作品无继承参数，已切换为来源解析模式', 'warn');
            return;
        }

        showToast('该作品缺少可继承参数', 'error');
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
        const inferred = Array.isArray(bp.lora_inferred_additional_prompts)
            ? bp.lora_inferred_additional_prompts
            : [];
        container.style.display = 'block';
        container.innerHTML = `
            <div style="background:#2a2a3e;border-radius:8px;padding:12px;font-size:12px;line-height:1.6;">
                <div style="font-size:14px;font-weight:bold;margin-bottom:8px;color:#a78bfa;">📌 ${bp.work_title || '未命名'}</div>
                <div style="margin-bottom:6px;"><b>Why Good:</b><br>${bp.why_good || ''}</div>
                <div style="margin-bottom:6px;"><b>Plain Text:</b><br><code style="font-size:11px;color:#94a3b8;">${bp.plain_text || ''}</code></div>
                <div style="margin-bottom:6px;"><b>Optimized Prompt:</b><br><code style="font-size:11px;color:#94a3b8;">${bp.output_prompt_plain || ''}</code></div>
                ${inferred.length ? `<div style="margin-bottom:6px;"><b>LoRA 反推附加提示词:</b><br><code style="font-size:11px;color:#67e8f9;">${inferred.join(', ')}</code></div>` : ''}
                ${bp.lora_dependency_notes ? `<div style="margin-bottom:6px;"><b>LoRA 依赖说明:</b><br>${bp.lora_dependency_notes}</div>` : ''}
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

        // 从 tracking 中获取当前图片关联的 favorite_id（而非全局变量）
        const filename = this.extractFilename(imageUrl);
        const batchId = filename.split('_')[0] || '';
        const trackInfo = this.tracking[batchId] || null;
        const favId = trackInfo ? trackInfo.favorite_id : '';

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
                    favorite_id: favId
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
                    <div id="aestheticPollStatus" style="font-size:11px;color:#666;margin-top:4px;">GPT-5.2 正在分析图片</div>
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
