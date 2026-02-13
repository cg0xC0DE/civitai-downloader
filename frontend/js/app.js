/**
 * Civitai Downloader + ComfyUI Draw Frontend
 */

class CivitaiApp {
    constructor() {
        this.api = this.detectApiBase();
        this.history = this.loadHistory();
        this.parsedData = null; // auto mode parsed result
        this.init();
    }

    // ===================== API Detection =====================
    detectApiBase() {
        const loc = window.location;
        // Behind nginx proxy or ngrok: use /civitaidl-service
        if (loc.pathname.startsWith('/civitaidl') || loc.hostname.includes('ngrok')) {
            return loc.origin + '/civitaidl-service';
        }
        // Local dev: frontend on any port -> backend on 53133
        return loc.protocol + '//' + loc.hostname + ':53133';
    }

    apiFetch(url, options = {}) {
        options.headers = Object.assign({
            'ngrok-skip-browser-warning': '1'
        }, options.headers || {});
        return fetch(url, options);
    }

    // ===================== Init =====================
    init() {
        // Store API base URL for other modules
        document.body.dataset.apiBase = this.api;

        this.bindTabs();
        this.initDownloadTab();
        this.initDrawTab();
        this.initFavoritesTab();
        this.renderHistory();
        this.checkComfyUIStatus();
        setInterval(() => this.checkComfyUIStatus(), 10000);
    }

    // ===================== Tab Switching =====================
    bindTabs() {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(btn.dataset.tab).classList.add('active');
            });
        });
    }

    // ===================== Download Tab =====================
    initDownloadTab() {
        const mainType = document.getElementById('dlMainType');
        const subType = document.getElementById('dlSubType');
        const form = document.getElementById('downloadForm');

        // Load subtypes on main type change
        mainType.addEventListener('change', () => this.loadDlSubtypes(mainType.value));
        // Initial load
        this.loadDlSubtypes(mainType.value);

        // Form submit
        form.addEventListener('submit', (e) => this.submitDownload(e));

        // 从后端查询是否有活跃的下载任务（任何设备都能看到进度）
        this.startTaskPolling();

        // 历史折叠/展开
        const toggle = document.getElementById('historyToggle');
        if (toggle) {
            toggle.addEventListener('click', () => {
                const list = document.getElementById('historyList');
                const arrow = toggle.querySelector('.toggle-arrow');
                const show = list.style.display === 'none';
                list.style.display = show ? 'block' : 'none';
                arrow.textContent = show ? '▼' : '▶';
            });
        }
    }

    async loadDlSubtypes(mainType) {
        const sel = document.getElementById('dlSubType');
        // embedding 无需子类型，直接存到根目录
        if (mainType === 'embedding') {
            sel.innerHTML = '<option value="_root" selected>（直接存入 embeddings）</option>';
            sel.style.display = 'none';
            return;
        }
        sel.style.display = '';
        sel.innerHTML = '<option value="">加载中...</option>';
        try {
            const res = await this.apiFetch(`${this.api}/api/subtypes?type=${mainType}`);
            const data = await res.json();
            if (data.status === 'success' && data.subtypes.length > 0) {
                sel.innerHTML = data.subtypes.map(s =>
                    `<option value="${s.key}">${s.key} (${s.count})</option>`
                ).join('');
            } else {
                sel.innerHTML = '<option value="">无子类型</option>';
            }
        } catch (e) {
            sel.innerHTML = '<option value="">加载失败</option>';
        }
    }

    async submitDownload(e) {
        e.preventDefault();
        const btn = e.target.querySelector('button[type="submit"]');
        this.setBtnLoading(btn, true);

        const urlInput = document.getElementById('dlUrl');
        const url = urlInput.value.trim();
        const mainType = document.getElementById('dlMainType').value;
        const subType = document.getElementById('dlSubType').value;

        if (!url || (!subType && mainType !== 'embedding')) {
            this.showDlResult({ status: 'error', message: '请填写URL并选择子类型' });
            this.setBtnLoading(btn, false);
            return;
        }

        try {
            const res = await this.apiFetch(`${this.api}/api/download`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, type: `${mainType}.${subType}`, auto_proxy: true })
            });
            const result = await res.json();

            if (result.task_id) {
                urlInput.value = '';
                this.startTaskPolling();
            } else {
                this.showDlResult(result);
            }
        } catch (err) {
            this.showDlResult({ status: 'error', message: err.message });
        } finally {
            this.setBtnLoading(btn, false);
        }
    }

    startTaskPolling() {
        if (this._polling) return;
        this._polling = true;
        this._pollActive();
    }

    async _pollActive() {
        try {
            const res = await this.apiFetch(`${this.api}/api/download/active`);
            const data = await res.json();
            const active = data.active || {};
            const recent = data.recent || {};
            const activeIds = Object.keys(active);

            if (activeIds.length > 0) {
                this._renderAllTasks(active);
                setTimeout(() => this._pollActive(), 800);
                return;
            }

            // 没有活跃任务了，显示最近完成的
            const recentIds = Object.keys(recent);
            if (recentIds.length > 0) {
                const last = recent[recentIds[recentIds.length - 1]];
                this.showDlResult(last);
                if (last.status === 'ok') {
                    this.addToHistory({ url: last.url, type: last.type || '', title: last.title || 'Unknown', status: last.status, time: new Date().toISOString() });
                }
            }
        } catch (e) { /* ignore */ }
        this._polling = false;
    }

    _renderAllTasks(tasks) {
        const div = document.getElementById('dlResult');
        const header = div.querySelector('.result-header');
        const icon = div.querySelector('.result-icon');
        const title = div.querySelector('.result-title');
        const body = div.querySelector('.result-body');
        div.style.display = 'block';

        const entries = Object.values(tasks);
        const downloading = entries.find(t => t.status === 'downloading');
        const queuedCount = entries.filter(t => t.status === 'queued').length;

        header.className = 'result-header downloading';
        icon.textContent = '⏬';
        const titleParts = [`${downloading ? (downloading.phase || '下载中') : '准备中'}`];
        if (queuedCount > 0) titleParts.push(`${queuedCount} 个排队`);
        title.textContent = titleParts.join(' · ');

        let html = '';
        for (const task of entries) {
            const taskUrl = task.url || '';
            const shortUrl = taskUrl.length > 55 ? taskUrl.slice(0, 52) + '...' : taskUrl;

            if (task.status === 'queued') {
                html += `<div style="padding:4px 0;border-top:1px solid var(--border);font-size:12px;color:var(--text-secondary);">⏳ 排队 · ${shortUrl}</div>`;
            } else {
                const pct = task.percent || 0;
                const dlMB = ((task.downloaded || 0) / 1048576).toFixed(1);
                const totalMB = ((task.total_size || 0) / 1048576).toFixed(1);
                const sizeText = task.total_size > 0 ? `${dlMB} / ${totalMB} MB` : '计算中...';
                html += `<div style="margin:4px 0;">
                    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:3px;word-break:break-all;">${shortUrl}</div>
                    <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px;"><span>${sizeText}</span><span>${pct}%</span></div>
                    <div style="height:8px;background:var(--border);border-radius:4px;overflow:hidden;">
                        <div style="height:100%;width:${pct}%;background:var(--accent);border-radius:4px;transition:width 0.3s;"></div>
                    </div></div>`;
            }
        }
        body.innerHTML = html;
    }

    showDlResult(data) {
        const div = document.getElementById('dlResult');
        const icon = div.querySelector('.result-icon');
        const title = div.querySelector('.result-title');
        const body = div.querySelector('.result-body');
        const header = div.querySelector('.result-header');
        div.style.display = 'block';

        if (data.status === 'ok') {
            header.className = 'result-header success';
            icon.textContent = '✅'; title.textContent = '下载成功';
            body.innerHTML = `<p><strong>模型:</strong> ${data.title}</p><p><strong>版本:</strong> ${data.version_name}</p><p><strong>文件:</strong> ${data.file_name}</p><p><strong>路径:</strong> ${data.saved_path || data.target_dir}</p>`;
        } else if (data.status === 'exists') {
            header.className = 'result-header warning';
            icon.textContent = '⚠️'; title.textContent = '模型已存在';
            const matchLabel = {exact:'精确匹配', file_contains:'文件名匹配', title_contains:'标题匹配'}[data.match_type] || data.match_type;
            body.innerHTML = `<p><strong>模型:</strong> ${data.title}</p><p><strong>匹配方式:</strong> ${matchLabel}</p><p><strong>已有文件:</strong> ${data.path || data.filename}</p>`;
        } else {
            header.className = 'result-header error';
            icon.textContent = '❌'; title.textContent = '下载失败';
            body.innerHTML = `<p>${data.message || '未知错误'}</p>`;
        }
    }

    // ===================== Draw Tab =====================
    initDrawTab() {
        // Mode switching
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                document.querySelectorAll('.draw-mode').forEach(m => m.classList.remove('active'));
                document.getElementById(btn.dataset.mode === 'auto' ? 'autoMode' : 'manualMode').classList.add('active');
            });
        });

        this.initAutoMode();
        this.initManualMode();

        // 重启 ComfyUI 按钮
        document.getElementById('restartComfyBtn').addEventListener('click', () => this.restartComfyUI());
    }

    // ---------- Auto Mode ----------
    initAutoMode() {
        this.loadWorkflowsInto(document.getElementById('autoWorkflowSelect'));
        document.getElementById('parseBtn').addEventListener('click', () => this.parseImage());
        document.getElementById('autoGenerateBtn').addEventListener('click', () => this.autoGenerate());
        
        // 清空 URL 按钮
        document.getElementById('clearImageUrl').addEventListener('click', () => {
            const input = document.getElementById('imageUrl');
            input.value = '';
            input.focus();
        });
        
        // 输入框有内容时显示清空按钮，无内容时隐藏
        const imageUrlInput = document.getElementById('imageUrl');
        const clearBtn = document.getElementById('clearImageUrl');
        const updateClearBtn = () => { clearBtn.textContent = imageUrlInput.value ? '✖' : ''; };
        imageUrlInput.addEventListener('input', updateClearBtn);
        updateClearBtn(); // 初始化状态

        // 工作流切换时显示/隐藏精绘参数
        const wfSelect = document.getElementById('autoWorkflowSelect');
        const upscaleBox = document.getElementById('upscaleParams');
        wfSelect.addEventListener('change', () => {
            upscaleBox.style.display = wfSelect.value.includes('upscale') ? 'block' : 'none';
        });
    }

    async parseImage() {
        const url = document.getElementById('imageUrl').value.trim();
        if (!url) return;

        const btn = document.getElementById('parseBtn');
        this.setBtnLoading(btn, true);

        try {
            const res = await this.apiFetch(`${this.api}/api/image/parse`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            const data = await res.json();

            if (data.status === 'error') {
                this.showParseError(data.message);
                return;
            }

            this.parsedData = data;
            this.showParseResult(data);
        } catch (err) {
            this.showParseError(err.message);
        } finally {
            this.setBtnLoading(btn, false);
        }
    }

    showParseResult(data) {
        const div = document.getElementById('parseResult');
        const autoBtn = document.getElementById('autoGenerateBtn');
        div.style.display = 'block';

        const isPartial = data.status === 'partial';
        const ps = data.param_sources || {};

        // partial 模式提示横幅
        const partialBanner = isPartial ? `
            <div style="background:#7c3aed22;border:1px solid #7c3aed;border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:13px;">
                <strong style="color:#a78bfa;">📝 手动模式</strong>
                <span style="color:var(--text-secondary);">该图片无生成参数，已提取模型列表。请在下方手工填入提示词和参数。</span>
            </div>` : '';

        // 参数来源状态徽标
        const _badge = (key) => {
            const s = ps[key];
            if (s === 'original')    return '<span class="ps-badge ps-original" title="精确获取">✅</span>';
            if (s === 'approximate') return '<span class="ps-badge ps-approx"   title="近似值">🔶</span>';
            if (s === 'ai_reverse')  return '<span class="ps-badge ps-reverse"  title="AI 反推">🤖</span>';
            if (s === 'default')     return '<span class="ps-badge ps-default"  title="使用默认">⚠️</span>';
            if (s === 'missing')     return '<span class="ps-badge ps-missing"  title="缺失">❌</span>';
            return '';
        };

        // Checkpoint 本地检查
        const ckptCheck = data.checks?.checkpoint;
        let ckptStatus;
        if (ckptCheck?.found) {
            ckptStatus = `<span class="check-ok">✅ ${ckptCheck.filename}</span>`;
        } else {
            const dlBtn = ckptCheck?.modelId ? ` <button class="btn-download-model" data-model-id="${ckptCheck.modelId}" data-version-id="${ckptCheck.modelVersionId}" data-type="ckpt">⬇️ 下载</button>` : '';
            ckptStatus = `<span class="check-fail">❌ 未找到</span>${dlBtn}
                <div style="margin-top:6px;">
                    <select id="ckptFallbackSelect" class="parse-edit parse-edit-text" style="width:100%;max-width:400px;padding:4px 8px;">
                        <option value="">-- 加载本地模型列表中... --</option>
                    </select>
                </div>`;
        }

        // LoRA 列表（权重可编辑）
        let lorasHtml = '';
        if (data.loras && data.loras.length > 0) {
            lorasHtml = data.checks.loras.map((lc, idx) => {
                let st;
                if (lc.found) {
                    st = `<span class="check-ok">✅ ${lc.filename}</span>`;
                } else if (lc.modelId) {
                    st = `<span class="check-fail">❌ 未找到</span> <button class="btn-download-model" data-model-id="${lc.modelId}" data-version-id="${lc.modelVersionId}" data-type="lora">⬇️ 下载</button>`;
                } else {
                    st = `<span class="check-fail">❌ 未找到</span>`;
                }
                const wSrc = lc.weight_source === 'default' ? ' <span title="权重未知，默认1.0" style="color:#f59e0b;">⚠️</span>' : '';
                return `<div class="parse-item"><span class="parse-label">${_badge('lora_weights')} LoRA: ${lc.requested_name} <input type="number" class="parse-edit parse-edit-weight" data-pe-field="lora_weight_${idx}" value="${lc.weight}" step="0.05" min="0" max="3">${wSrc}</span><span class="parse-value">${st}</span></div>`;
            }).join('');
        }

        // Embedding 列表（含磁盘检查）
        let embHtml = '';
        const embChecks = (data.checks && data.checks.embeddings) || [];
        if (embChecks.length > 0) {
            embHtml = embChecks.map(ec => {
                let st;
                if (ec.found) {
                    st = `<span class="check-ok">✅ ${ec.filename}</span>`;
                } else if (ec.modelId) {
                    st = `<span class="check-fail">❌ 未找到</span> <button class="btn-download-model" data-model-id="${ec.modelId}" data-version-id="${ec.modelVersionId}" data-type="embedding">⬇️ 下载</button>`;
                } else {
                    st = `<span class="check-fail">❌ 未找到（无 Civitai 链接）</span>`;
                }
                return `<div class="parse-item"><span class="parse-label">${_badge('embeddings')} Embedding: ${this.escapeHtml(ec.requested_name)}</span><span class="parse-value">${st}</span></div>`;
            }).join('');
        }

        // 复刻完整度总结
        const summary = ps._summary || {};
        const total = summary.total || 0;
        const orig = summary.original || 0;
        const pct = total > 0 ? Math.round(orig / total * 100) : 0;
        const barColor = pct >= 80 ? '#22c55e' : pct >= 50 ? '#f59e0b' : '#ef4444';

        // 尺寸注释
        const sizeNote = data._size_note ? ` <span style="color:#f59e0b;font-size:11px;">(${data._size_note})</span>` : '';

        div.innerHTML = `
            <div class="parse-result">
                <h4>${isPartial ? '📝 手动填写参数' : '解析结果'}</h4>
                ${partialBanner}
                ${!isPartial ? `<div class="parse-summary" style="margin-bottom:10px;padding:8px 12px;background:#1a1a2e;border-radius:8px;font-size:12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <span>复刻完整度</span>
                        <span style="font-weight:bold;color:${barColor}">${orig}/${total} 参数精确获取 (${pct}%)</span>
                    </div>
                    <div style="height:6px;background:#333;border-radius:3px;overflow:hidden;">
                        <div style="height:100%;width:${pct}%;background:${barColor};border-radius:3px;transition:width .3s;"></div>
                    </div>
                    <div style="display:flex;gap:12px;margin-top:4px;color:#fff;flex-wrap:wrap;">
                        ${summary.approximate ? `<span>🔶 近似 ${summary.approximate} 项</span>` : ''}
                        ${summary.ai_reverse ? `<span>🤖 AI反推 ${summary.ai_reverse} 项</span>` : ''}
                        ${summary.default ? `<span>⚠️ 默认 ${summary.default} 项</span>` : ''}
                        ${summary.missing ? `<span>❌ 缺失 ${summary.missing} 项</span>` : ''}
                    </div>
                </div>` : ''}
                <div class="parse-item"><span class="parse-label">${_badge('checkpoint')} Checkpoint</span><span class="parse-value">${data.checkpoint || '未知'}</span></div>
                <div class="parse-item"><span class="parse-label">本地仓库</span><span class="parse-value">${ckptStatus}</span></div>
                ${lorasHtml}
                ${embHtml}
                <div class="parse-item"><span class="parse-label">${_badge('sampler')} 采样器</span><span class="parse-value"><input type="text" class="parse-edit parse-edit-text" data-pe-field="sampler" value="${this.escapeAttr(data.sampler || 'dpmpp_2m')}" style="width:100px;"> / <input type="text" class="parse-edit parse-edit-text" data-pe-field="scheduler" value="${this.escapeAttr(data.scheduler || '')}" style="width:80px;" placeholder="scheduler"></span></div>
                <div class="parse-item"><span class="parse-label">${_badge('steps')} 步数</span><span class="parse-value"><input type="number" class="parse-edit parse-edit-num" data-pe-field="steps" value="${data.steps || 20}" min="1" max="150"></span></div>
                <div class="parse-item"><span class="parse-label">${_badge('cfg')} CFG</span><span class="parse-value"><input type="number" class="parse-edit parse-edit-num" data-pe-field="cfg" value="${data.cfg || 7}" step="0.5" min="1" max="30"></span></div>
                <div class="parse-item"><span class="parse-label">${_badge('size')} 尺寸${sizeNote}</span><span class="parse-value"><span class="parse-size-group"><input type="number" class="parse-edit parse-edit-size" data-pe-field="width" value="${data.width || 1024}" step="64" min="64" max="4096"> × <input type="number" class="parse-edit parse-edit-size" data-pe-field="height" value="${data.height || 1024}" step="64" min="64" max="4096"></span></span></div>
                <div class="parse-item"><span class="parse-label">${_badge('seed')} Seed</span><span class="parse-value"><input type="number" class="parse-edit" data-pe-field="seed" value="${data.seed >= 0 ? data.seed : -1}" style="width:130px;text-align:center;" min="-1"></span></div>
                ${data.clip_skip != null ? `<div class="parse-item"><span class="parse-label">${_badge('clip_skip')} Clip Skip</span><span class="parse-value"><input type="number" class="parse-edit parse-edit-num" data-pe-field="clip_skip" value="${data.clip_skip}" min="-5" max="0"></span></div>` : ''}
                ${data.prompt_reverse_tagged ? `
                <div style="background:#06b6d422;border:1px solid #06b6d4;border-radius:8px;padding:10px 14px;margin:8px 0;font-size:13px;">
                    <strong style="color:#22d3ee;">🤖 AI 反推提示词</strong>
                    <span style="color:var(--text-secondary);">原图无提示词，已通过 ${data.reverse_tag_provider === 'gemini' ? 'Gemini' : 'GPT'} 反推 danbooru 标签${data.trigger_words && data.trigger_words.length > 0 ? `，并自动添加了 ${data.trigger_words.length} 个模型触发词` : ''}。可在下方编辑后生成。</span>
                </div>` : ''}
                <div style="margin-top:8px;"><strong style="font-size:13px;">${_badge('prompt')} Prompt:</strong><textarea class="parse-edit-prompt" data-pe-field="prompt">${this.escapeHtml(data.prompt || '')}</textarea></div>
                <div style="margin-top:8px;"><strong style="font-size:13px;">${_badge('negative_prompt')} Negative:</strong><textarea class="parse-edit-prompt" data-pe-field="negative_prompt" style="min-height:40px;">${this.escapeHtml(data.negative_prompt || '')}</textarea></div>
                ${data.variations && data.variations.length > 0 ? `
                <div style="margin-top:10px;padding:8px 12px;background:#1a1a2e;border-radius:8px;">
                    <strong style="font-size:13px;">🎯 出图计划（${data.total_images} 张）</strong>
                    <div style="margin-top:6px;font-size:12px;">
                        ${data.variations.map((v, i) => {
                            const p = v.params || {};
                            const details = [];
                            if (p.sampler) details.push(p.sampler);
                            if (p.scheduler) details.push(p.scheduler);
                            if (p.width && p.height) details.push(p.width + '×' + p.height);
                            return '<div style="padding:2px 0;"><span style="color:#60a5fa;">#' + (i+1) + '</span> ' + v.label + (details.length ? ' <span style="color:#888;">(' + details.join(', ') + ')</span>' : '') + '</div>';
                        }).join('')}
                    </div>
                </div>` : ''}
            </div>
        `;

        // 绑定下载按钮事件
        div.querySelectorAll('.btn-download-model').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const modelId = btn.dataset.modelId;
                const versionId = btn.dataset.versionId;
                const type = btn.dataset.type;
                this.triggerModelDownload(modelId, versionId, type, btn);
            });
        });

        // 只要 checkpoint 可用就允许生成（缺失的 LoRA 会自动跳过）
        autoBtn.disabled = !(data.checks?.checkpoint?.found || document.getElementById('ckptFallbackSelect'));

        // 如果 checkpoint 未找到，异步加载本地模型列表填充下拉框
        if (!ckptCheck?.found) {
            this._loadCheckpointDropdown(autoBtn, data);
        }
    }

    async _loadCheckpointDropdown(autoBtn, data) {
        const sel = document.getElementById('ckptFallbackSelect');
        if (!sel) return;
        try {
            const res = await this.apiFetch(`${this.api}/api/comfyui/checkpoints`);
            const json = await res.json();
            const ckpts = json.checkpoints || [];
            if (ckpts.length === 0) {
                sel.innerHTML = '<option value="">-- 无可用模型 --</option>';
                return;
            }
            sel.innerHTML = '<option value="">-- 请选择基模 --</option>' +
                ckpts.map(c => `<option value="${this.escapeAttr(c)}">${this.escapeHtml(c)}</option>`).join('');
            sel.addEventListener('change', () => {
                autoBtn.disabled = !sel.value;
            });
        } catch {
            sel.innerHTML = '<option value="">-- 加载失败 --</option>';
        }
    }

    async triggerModelDownload(modelId, versionId, type, btn) {
        const url = `https://civitai.com/models/${modelId}?modelVersionId=${versionId}`;

        // 切换到下载 tab
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.querySelector('.tab-btn[data-tab="download"]').classList.add('active');
        document.getElementById('download').classList.add('active');

        // 设置类型并加载子类型列表
        const mainTypeSelect = document.getElementById('dlMainType');
        mainTypeSelect.value = type;
        await this.loadDlSubtypes(type);

        // 填入 URL，用户自行选择 subtype 后手动提交
        document.getElementById('dlUrl').value = url;

        btn.textContent = '✅ 已填入';
        btn.style.color = '#16a34a';
    }

    showParseError(msg) {
        const div = document.getElementById('parseResult');
        div.style.display = 'block';
        div.innerHTML = `<div class="parse-result"><div style="color:#dc2626;">❌ ${this.escapeHtml(msg)}</div></div>`;
        document.getElementById('autoGenerateBtn').disabled = true;
    }

    async autoGenerate() {
        if (!this.parsedData) return;
        const _ckptSel = document.getElementById('ckptFallbackSelect');
        const _hasCkpt = this.parsedData.checks?.checkpoint?.found || (_ckptSel && _ckptSel.value);
        if (!_hasCkpt) return;

        const btn = document.getElementById('autoGenerateBtn');
        this.setBtnLoading(btn, true);

        const d = this.parsedData;
        const sourceUrl = document.getElementById('imageUrl').value.trim();
        const ckpt = d.checks.checkpoint;
        // 如果用户从下拉框选择了 checkpoint，覆盖
        const ckptFallback = document.getElementById('ckptFallbackSelect');
        // 构建 LoRA 列表（从 DOM 读取可能被用户修改的权重）
        const loraList = (d.checks.loras || [])
            .filter(lc => lc.found)
            .map((lc, idx) => {
                const editedWeight = parseFloat(this._peVal(`lora_weight_${idx}`));
                return { name: `${lc.subtype}/${lc.filename}`, weight: isNaN(editedWeight) ? (lc.weight || 1.0) : editedWeight };
            });

        // 从可编辑 DOM 字段读取（用户可能已手工修改）
        const _pv = (field, fallback) => { const v = this._peVal(field); return v !== null && v !== '' ? v : fallback; };
        const seed = parseInt(_pv('seed', -1));

        const payload = {
            workflow: document.getElementById('autoWorkflowSelect').value || 'nolora',
            checkpoint: ckpt.found ? `${ckpt.subtype}/${ckpt.filename}` : (ckptFallback?.value || ''),
            prompt: _pv('prompt', d.prompt),
            negative_prompt: _pv('negative_prompt', d.negative_prompt || 'low quality, worst quality'),
            width: parseInt(_pv('width', d.width)), height: parseInt(_pv('height', d.height)),
            steps: parseInt(_pv('steps', d.steps || 20)), cfg: parseFloat(_pv('cfg', d.cfg || 7)),
            sampler: _pv('sampler', d.sampler || 'dpmpp_2m'),
            scheduler: _pv('scheduler', d.scheduler || ''),
            seed: seed > 0 ? seed : null,
            loras: loraList.length > 0 ? loraList : undefined,
            variations: d.variations || null,
            favorite_id: window._activeFavoriteId || '',
            source_url: sourceUrl
        };

        // 精绘工作流时附加 denoise 参数
        if (payload.workflow.includes('upscale')) {
            payload.upscale_denoise = [
                parseFloat(document.getElementById('upscaleDenoise1').value) || 0.44,
                parseFloat(document.getElementById('upscaleDenoise2').value) || 0.44,
                parseFloat(document.getElementById('upscaleDenoise3').value) || 0.44,
            ];
        }

        try {
            const res = await this.apiFetch(`${this.api}/api/workflow/run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            if (result.status === 'submitted' && result.prompt_id) {
                this.showDrawProgress(result.prompt_id, result.batch_size);
                this.setBtnLoading(btn, false);
                this.pollGenStatus(result.prompt_id, sourceUrl);
            } else {
                this.showDrawResult(result, sourceUrl);
                this.setBtnLoading(btn, false);
            }
        } catch (err) {
            this.showDrawResult({ status: 'error', message: err.message }, sourceUrl);
            this.setBtnLoading(btn, false);
        }
    }

    // ---------- Manual Mode ----------
    initManualMode() {
        // Load workflows
        this.loadWorkflowsInto(document.getElementById('workflowSelect'));

        // Checkpoint two-level
        const ckptSub = document.getElementById('ckptSubtype');
        const ckptFile = document.getElementById('ckptFile');
        this.loadSubtypes('ckpt', ckptSub).then(() => {
            if (ckptSub.value) this.loadFiles('ckpt', ckptSub.value, ckptFile);
        });
        ckptSub.addEventListener('change', () => this.loadFiles('ckpt', ckptSub.value, ckptFile));

        // LoRA two-level
        const loraSub = document.getElementById('loraSubtype');
        const loraFile = document.getElementById('loraFile');
        this.loadSubtypes('lora', loraSub).then(() => {
            if (loraSub.value) this.loadFiles('lora', loraSub.value, loraFile);
        });
        loraSub.addEventListener('change', () => this.loadFiles('lora', loraSub.value, loraFile));

        // LoRA toggle
        document.getElementById('useLora').addEventListener('change', function () {
            document.getElementById('loraSettings').classList.toggle('hidden', !this.checked);
        });

        // LoRA strength slider
        const loraStrength = document.getElementById('loraStrength');
        const loraDisplay = document.getElementById('loraStrengthDisplay');
        loraStrength.addEventListener('input', () => { loraDisplay.textContent = loraStrength.value; });

        // Size buttons
        document.querySelectorAll('.size-btn').forEach(btn => {
            btn.addEventListener('click', function () {
                document.querySelectorAll('.size-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                document.getElementById('width').value = this.dataset.w;
                document.getElementById('height').value = this.dataset.h;
            });
        });

        // Form submit
        document.getElementById('drawForm').addEventListener('submit', (e) => this.manualGenerate(e));
    }

    async loadWorkflowsInto(sel) {
        sel.innerHTML = '<option value="">加载中...</option>';
        try {
            const res = await this.apiFetch(`${this.api}/api/workflows`);
            const data = await res.json();
            if (data.status === 'success' && data.items.length > 0) {
                sel.innerHTML = data.items.map(w => {
                    const label = w.source === 'comfyui' ? `${w.name} (ComfyUI)` : w.name;
                    return `<option value="${w.name}">${label}</option>`;
                }).join('');
            } else {
                sel.innerHTML = '<option value="nolora">nolora</option>';
            }
        } catch (e) {
            console.error('加载工作流失败:', e);
            sel.innerHTML = '<option value="nolora">nolora</option>';
        }
    }

    async loadSubtypes(mainType, selectEl) {
        selectEl.innerHTML = '<option value="">加载中...</option>';
        try {
            const res = await this.apiFetch(`${this.api}/api/subtypes?type=${mainType}`);
            const data = await res.json();
            if (data.status === 'success' && data.subtypes.length > 0) {
                selectEl.innerHTML = data.subtypes.map(s =>
                    `<option value="${s.key}">${s.key} (${s.count})</option>`
                ).join('');
            } else {
                selectEl.innerHTML = '<option value="">无数据</option>';
            }
        } catch (e) {
            selectEl.innerHTML = '<option value="">加载失败</option>';
        }
    }

    async loadFiles(mainType, subtype, selectEl) {
        if (!subtype) return;
        selectEl.innerHTML = '<option value="">加载中...</option>';
        try {
            const res = await this.apiFetch(`${this.api}/api/files?type=${mainType}&subtype=${encodeURIComponent(subtype)}`);
            const data = await res.json();
            if (data.status === 'success' && data.files.length > 0) {
                selectEl.innerHTML = data.files.map(f => {
                    const sizeMB = (f.size / 1048576).toFixed(0);
                    return `<option value="${subtype}/${f.filename}">${f.filename} (${sizeMB}MB)</option>`;
                }).join('');
            } else {
                selectEl.innerHTML = '<option value="">无文件</option>';
            }
        } catch (e) {
            selectEl.innerHTML = '<option value="">加载失败</option>';
        }
    }

    async manualGenerate(e) {
        e.preventDefault();
        const btn = document.getElementById('manualGenerateBtn');
        this.setBtnLoading(btn, true);

        const payload = {
            workflow: document.getElementById('workflowSelect').value || 'nolora',
            checkpoint: document.getElementById('ckptFile').value,
            prompt: document.getElementById('positivePrompt').value,
            negative_prompt: document.getElementById('negativePrompt').value || 'low quality, worst quality',
            width: parseInt(document.getElementById('width').value),
            height: parseInt(document.getElementById('height').value),
            steps: 20, cfg: 7.0, sampler: 'dpmpp_2m'
        };

        if (document.getElementById('useLora').checked) {
            const loraVal = document.getElementById('loraFile').value;
            if (loraVal) {
                payload.loras = [{
                    name: loraVal,
                    weight: parseFloat(document.getElementById('loraStrength').value)
                }];
            }
        }

        try {
            const res = await this.apiFetch(`${this.api}/api/workflow/run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            if (result.status === 'submitted' && result.prompt_id) {
                this.showDrawProgress(result.prompt_id, result.batch_size);
                this.setBtnLoading(btn, false);
                this.pollGenStatus(result.prompt_id, null);
            } else {
                this.showDrawResult(result);
                this.setBtnLoading(btn, false);
            }
        } catch (err) {
            this.showDrawResult({ status: 'error', message: err.message });
            this.setBtnLoading(btn, false);
        }
    }

    // ===================== Shared: Draw Progress & Polling =====================
    showDrawProgress(promptId, batchSize) {
        const div = document.getElementById('drawResult');
        const header = div.querySelector('.result-header');
        const body = div.querySelector('.result-body');
        div.style.display = 'block';
        header.className = 'result-header';
        header.querySelector('.result-icon').textContent = '⏳';
        header.querySelector('.result-title').textContent = `批量生成中 (${batchSize || 4} 张)...`;
        body.innerHTML = `<p>批次: ${promptId} | <span class="loading-spinner"></span> 等待 ComfyUI 完成</p>`;
    }

    async pollGenStatus(promptId, sourceUrl) {
        const poll = async () => {
            try {
                const res = await this.apiFetch(`${this.api}/api/workflow/status?prompt_id=${promptId}`);
                const data = await res.json();
                if (data.status === 'running') {
                    // 更新进度显示
                    const body = document.querySelector('#drawResult .result-body');
                    if (body && data.completed !== undefined) {
                        body.innerHTML = `<p>批次: ${promptId} | 已完成 ${data.completed}/${data.batch_size} | <span class="loading-spinner"></span> 等待中</p>`;
                    }
                    setTimeout(poll, 2000);
                    return;
                }
                // done (success or error)
                this.showDrawResult(data, sourceUrl);
            } catch (err) {
                this.showDrawResult({ status: 'error', message: err.message }, sourceUrl);
            }
        };
        setTimeout(poll, 2000);
    }

    // ===================== Shared: Draw Result =====================
    showDrawResult(result, sourceUrl) {
        const div = document.getElementById('drawResult');
        const header = div.querySelector('.result-header');
        const body = div.querySelector('.result-body');
        div.style.display = 'block';

        if (result.status === 'success') {
            header.className = 'result-header success';
            header.querySelector('.result-icon').textContent = '✅';
            header.querySelector('.result-title').textContent = `生成成功 (${result.images_count}张)`;
            const taskId = result.batch_id || result.prompt_id || '';
            body.innerHTML = `<p>批次: ${taskId} | 图片已保存到 backend/output/</p>`;
            // 记录到生成历史
            this.addGenHistory(sourceUrl, true, result.images_count, taskId);
        } else {
            header.className = 'result-header error';
            header.querySelector('.result-icon').textContent = '❌';
            header.querySelector('.result-title').textContent = '生成失败';
            body.innerHTML = `<p>${result.message || result.error || '未知错误'}</p>`;
            this.addGenHistory(sourceUrl, false, 0);
        }
    }

    // ===================== Generation History =====================
    addGenHistory(sourceUrl, success, count, promptId) {
        if (!this._genHistory) this._genHistory = [];
        this._genHistory.unshift({
            url: sourceUrl || '',
            success,
            count,
            promptId: promptId || '',
            time: new Date().toLocaleTimeString()
        });
        // 最多保留 20 条
        if (this._genHistory.length > 20) this._genHistory.pop();
        this.renderGenHistory();
    }

    renderGenHistory() {
        const container = document.getElementById('genHistory');
        const list = document.getElementById('genHistoryList');
        if (!this._genHistory || this._genHistory.length === 0) {
            container.style.display = 'none';
            return;
        }
        container.style.display = 'block';
        list.innerHTML = this._genHistory.map(h => {
            const icon = h.success ? '✅' : '❌';
            const info = h.success ? `${h.count}张 · ${h.time}` : `失败 · ${h.time}`;
            const urlHtml = h.url
                ? `<a class="gh-url" href="${this.escapeHtml(h.url)}" target="_blank" title="${this.escapeHtml(h.url)}">${this.escapeHtml(h.url)}</a>`
                : `<span class="gh-url" style="color:var(--text-secondary);">手动模式</span>`;
            return `<div class="gen-history-item"><span class="gh-status">${icon}</span>${urlHtml}<span class="gh-info">${info}</span></div>`;
        }).join('');
    }

    // ===================== ComfyUI Status =====================
    async checkComfyUIStatus() {
        const badge = document.getElementById('comfyuiStatus');
        const text = document.getElementById('statusText');
        if (!badge || !text) return;
        try {
            const res = await this.apiFetch(`${this.api}/api/comfyui/queue`);
            const data = await res.json();
            if (data.status === 'success') {
                badge.className = 'status-badge success';
                text.textContent = 'ComfyUI 已就绪';
            } else {
                badge.className = 'status-badge error';
                text.textContent = 'ComfyUI 未就绪';
            }
        } catch {
            badge.className = 'status-badge error';
            text.textContent = 'ComfyUI 未连接';
        }
    }

    async restartComfyUI() {
        const btn = document.getElementById('restartComfyBtn');
        const badge = document.getElementById('comfyuiStatus');
        const text = document.getElementById('statusText');
        btn.disabled = true;
        btn.textContent = '⏳';
        badge.className = 'status-badge idle';
        text.textContent = '正在重启...';

        try {
            const res = await this.apiFetch(`${this.api}/api/comfyui/restart`, { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                // 轮询等待 ComfyUI 恢复就绪
                let attempts = 0;
                const waitReady = setInterval(async () => {
                    attempts++;
                    text.textContent = `等待重启... (${attempts * 3}s)`;
                    try {
                        const r = await this.apiFetch(`${this.api}/api/comfyui/queue`);
                        const d = await r.json();
                        if (d.status === 'success') {
                            clearInterval(waitReady);
                            badge.className = 'status-badge success';
                            text.textContent = 'ComfyUI 已就绪';
                            btn.disabled = false;
                            btn.textContent = '🔄';
                        }
                    } catch {}
                    if (attempts >= 40) {
                        clearInterval(waitReady);
                        badge.className = 'status-badge error';
                        text.textContent = '重启超时，请检查 watchdog';
                        btn.disabled = false;
                        btn.textContent = '🔄';
                    }
                }, 3000);
            } else {
                text.textContent = data.message || '重启失败';
                btn.disabled = false;
                btn.textContent = '🔄';
            }
        } catch (err) {
            badge.className = 'status-badge error';
            text.textContent = '重启请求失败';
            btn.disabled = false;
            btn.textContent = '🔄';
        }
    }

    // ===================== History =====================
    loadHistory() {
        try { return JSON.parse(localStorage.getItem('dl_history') || '[]'); }
        catch { return []; }
    }

    addToHistory(item) {
        this.history.unshift(item);
        if (this.history.length > 50) this.history.pop();
        localStorage.setItem('dl_history', JSON.stringify(this.history));
        this.renderHistory();
    }

    renderHistory() {
        const el = document.getElementById('historyList');
        if (!el) return;
        if (this.history.length === 0) {
            el.innerHTML = '<p class="empty-state">暂无下载记录</p>';
            return;
        }
        el.innerHTML = this.history.slice(0, 20).map(item => {
            const icon = item.status === 'ok' ? '✅' : item.status === 'exists' ? '⚠️' : '❌';
            const t = new Date(item.time).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
            return `<div class="history-item"><span class="history-icon">${icon}</span><div class="history-info"><div class="history-title">${this.escapeHtml(item.title)}</div><div class="history-meta">${item.type} · ${t}</div></div></div>`;
        }).join('');
    }

    // ===================== Favorites Tab =====================
    initFavoritesTab() {
        this._favAllItems = [];  // 全量数据
        this._favItems = [];     // 当前筛选后的视图
        this._favIndex = 0;
        this._favFilter = 'all';
        this._favThumbCache = {}; // imageId -> thumb_url

        document.getElementById('favRefreshBtn').addEventListener('click', () => this.loadFavorites());
        document.getElementById('favPrevBtn').addEventListener('click', () => this.favNav(-1));
        document.getElementById('favNextBtn').addEventListener('click', () => this.favNav(1));
        document.getElementById('favReplicateBtn').addEventListener('click', () => {
            const item = this._favItems[this._favIndex];
            if (item) this.goToDrawWithUrl(item.url, item.id);
        });
        document.getElementById('favDeleteBtn').addEventListener('click', () => this.favDeleteCurrent());

        // 状态筛选下拉框
        document.getElementById('favFilterSelect').addEventListener('change', (e) => {
            this._setFavFilter(e.target.value);
        });

        // 猜你喜欢：随机跳转
        document.getElementById('favRandomBtn').addEventListener('click', () => this.favRandom());

        // 切换到收藏 tab 时自动加载
        document.querySelector('.tab-btn[data-tab="favorites"]').addEventListener('click', () => {
            if (!this._favLoaded) this.loadFavorites();
        });
    }

    _setFavFilter(status) {
        this._favFilter = status;
        document.getElementById('favFilterSelect').value = status;
        this._applyFavFilter();
    }

    favRandom() {
        if (this._favItems.length <= 1) return;
        let newIdx;
        do { newIdx = Math.floor(Math.random() * this._favItems.length); } while (newIdx === this._favIndex);
        this._favIndex = newIdx;
        this.favRenderCurrent();
    }

    _applyFavFilter() {
        const f = this._favFilter;
        this._favItems = f === 'all'
            ? [...this._favAllItems]
            : this._favAllItems.filter(i => (i.status || 'pending') === f);
        this._favIndex = 0;
        this._updateFavFilterCounts();

        const empty = document.getElementById('favEmptyState');
        if (this._favItems.length === 0) {
            document.getElementById('favImageContainer').innerHTML = '';
            document.getElementById('favActions').style.display = 'none';
            empty.style.display = 'block';
            this.favUpdateCounter();
        } else {
            empty.style.display = 'none';
            this.favRenderCurrent();
        }
    }

    _updateFavFilterCounts() {
        const counts = { all: this._favAllItems.length, pending: 0, processing: 0, done: 0, fail: 0 };
        const labels = { all: '全部', pending: '待处理', processing: '处理中', done: '已分析', fail: '复刻失败' };
        this._favAllItems.forEach(i => { const s = i.status || 'pending'; if (counts[s] !== undefined) counts[s]++; });
        const sel = document.getElementById('favFilterSelect');
        Array.from(sel.options).forEach(opt => {
            const s = opt.value;
            opt.textContent = `${labels[s]} ${counts[s] || 0}`;
        });
    }

    async loadFavorites() {
        const container = document.getElementById('favImageContainer');
        const loading = document.getElementById('favLoading');
        const empty = document.getElementById('favEmptyState');
        const actions = document.getElementById('favActions');

        container.innerHTML = '';
        actions.style.display = 'none';
        loading.style.display = 'block';
        empty.style.display = 'none';

        try {
            const res = await this.apiFetch(`${this.api}/api/favorite/list`);
            const data = await res.json();

            loading.style.display = 'none';
            this._favAllItems = data.items || [];
            this._favLoaded = true;

            this._applyFavFilter();
        } catch (err) {
            loading.style.display = 'none';
            container.innerHTML = `<div style="text-align:center;color:var(--error);padding:24px;">加载失败: ${this.escapeHtml(err.message)}</div>`;
        }
    }

    favNav(delta) {
        const len = this._favItems.length;
        if (len === 0) return;
        this._favIndex = (this._favIndex + delta + len) % len;
        this.favRenderCurrent();
    }

    favUpdateCounter() {
        const total = this._favItems.length;
        const current = total > 0 ? this._favIndex + 1 : 0;
        document.getElementById('favCounter').textContent = `${current}/${total}`;
    }

    async favRenderCurrent() {
        const item = this._favItems[this._favIndex];
        if (!item) return;

        const container = document.getElementById('favImageContainer');
        const actions = document.getElementById('favActions');
        const openLink = document.getElementById('favOpenLink');

        this.favUpdateCounter();

        // 设置 Civitai 链接
        openLink.href = item.url || '#';

        // 先显示 loading placeholder
        container.innerHTML = `<div style="text-align:center;padding:40px;"><div class="loading-spinner"></div></div>`;
        actions.style.display = 'none';

        const imageId = item.image_id;
        if (!imageId) {
            container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-secondary);">无效的图片 ID</div>`;
            return;
        }

        // 获取缩略图（带缓存）
        let thumbUrl = this._favThumbCache[imageId];
        if (!thumbUrl) {
            try {
                const res = await this.apiFetch(`${this.api}/api/image/thumb?id=${imageId}`);
                const data = await res.json();
                if (data.status === 'ok' && data.thumb_url) {
                    thumbUrl = data.thumb_url;
                    this._favThumbCache[imageId] = thumbUrl;
                }
            } catch { /* ignore */ }
        }

        if (thumbUrl) {
            container.innerHTML = `<img src="${thumbUrl}" alt="#${imageId}" class="gallery-image"
                onerror="this.parentElement.innerHTML='<div style=\\'text-align:center;padding:40px;color:var(--text-secondary)\\'>图片加载失败</div>'">`;
            actions.style.display = 'flex';
        } else {
            container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-secondary);">无法获取缩略图</div>`;
            actions.style.display = 'flex';
        }
    }

    async favDeleteCurrent() {
        const item = this._favItems[this._favIndex];
        if (!item || !item.id) return;
        if (!confirm('确定删除这条收藏？')) return;

        const btn = document.getElementById('favDeleteBtn');
        btn.disabled = true;
        btn.textContent = '删除中...';

        try {
            const res = await this.apiFetch(`${this.api}/api/favorite/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: item.id })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                // 从全量列表和筛选列表中都移除
                const allIdx = this._favAllItems.findIndex(i => i.id === item.id);
                if (allIdx >= 0) this._favAllItems.splice(allIdx, 1);
                this._favItems.splice(this._favIndex, 1);
                if (this._favIndex >= this._favItems.length) {
                    this._favIndex = Math.max(0, this._favItems.length - 1);
                }
                this._updateFavFilterCounts();
                if (this._favItems.length === 0) {
                    document.getElementById('favImageContainer').innerHTML = '';
                    document.getElementById('favActions').style.display = 'none';
                    document.getElementById('favEmptyState').style.display = 'block';
                    this.favUpdateCounter();
                } else {
                    this.favRenderCurrent();
                }
            } else {
                alert('删除失败: ' + (data.message || ''));
            }
        } catch (err) {
            alert('删除失败: ' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = '🗑️ 删除';
        }
    }

    goToDrawWithUrl(civitaiUrl, favoriteId) {
        // 先立即切换到绘图 tab（不阻塞 UI）
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.querySelector('.tab-btn[data-tab="draw"]').classList.add('active');
        document.getElementById('draw').classList.add('active');

        // 确保处于 auto mode
        document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
        document.querySelector('.mode-btn[data-mode="auto"]').classList.add('active');
        document.querySelectorAll('.draw-mode').forEach(m => m.classList.remove('active'));
        document.getElementById('autoMode').classList.add('active');

        // 填入 URL 并自动触发解析
        const input = document.getElementById('imageUrl');
        input.value = civitaiUrl;
        input.dispatchEvent(new Event('input'));
        setTimeout(() => this.parseImage(), 100);

        // 异步更新收藏状态（不阻塞 tab 切换）
        if (favoriteId) {
            window._activeFavoriteId = favoriteId;
            const item = this._favAllItems.find(i => i.id === favoriteId);
            if (item) item.status = 'processing';
            // 同步更新筛选视图中的对应项
            const viewItem = this._favItems.find(i => i.id === favoriteId);
            if (viewItem && viewItem !== item) viewItem.status = 'processing';
            this._updateFavFilterCounts();
            this.favUpdateCounter();
            this.apiFetch(`${this.api}/api/favorite/update-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: favoriteId, status: 'processing' })
            }).catch(() => {});
        } else {
            window._activeFavoriteId = null;
        }
    }

    // ===================== Utilities =====================
    setBtnLoading(btn, loading) {
        if (!btn) return;
        const text = btn.querySelector('.btn-text');
        const spinner = btn.querySelector('.btn-loading');
        btn.disabled = loading;
        if (text) text.style.display = loading ? 'none' : 'inline';
        if (spinner) spinner.style.display = loading ? 'inline' : 'none';
    }

    escapeHtml(str) {
        const d = document.createElement('div');
        d.textContent = str || '';
        return d.innerHTML;
    }

    escapeAttr(str) {
        return (str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    _peVal(field) {
        const el = document.querySelector(`[data-pe-field="${field}"]`);
        return el ? el.value : null;
    }
}

document.addEventListener('DOMContentLoaded', () => { window.app = new CivitaiApp(); });
