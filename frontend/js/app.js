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
        this.bindTabs();
        this.initDownloadTab();
        this.initDrawTab();
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

        if (!url || !subType) {
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

            if (data.status !== 'success') {
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

        const ckptCheck = data.checks?.checkpoint;
        let ckptStatus;
        if (ckptCheck?.found) {
            ckptStatus = `<span class="check-ok">✅ ${ckptCheck.filename}</span>`;
        } else if (ckptCheck?.modelId) {
            ckptStatus = `<span class="check-fail">❌ 未找到</span> <button class="btn-download-model" data-model-id="${ckptCheck.modelId}" data-version-id="${ckptCheck.modelVersionId}" data-type="ckpt">⬇️ 下载</button>`;
        } else {
            ckptStatus = `<span class="check-fail">❌ 未找到</span>`;
        }

        let lorasHtml = '';
        if (data.loras && data.loras.length > 0) {
            lorasHtml = data.checks.loras.map(lc => {
                let st;
                if (lc.found) {
                    st = `<span class="check-ok">✅ ${lc.filename}</span>`;
                } else if (lc.modelId) {
                    st = `<span class="check-fail">❌ 未找到</span> <button class="btn-download-model" data-model-id="${lc.modelId}" data-version-id="${lc.modelVersionId}" data-type="lora">⬇️ 下载</button>`;
                } else {
                    st = `<span class="check-fail">❌ 未找到</span>`;
                }
                return `<div class="parse-item"><span class="parse-label">LoRA: ${lc.requested_name} (${lc.weight})</span><span class="parse-value">${st}</span></div>`;
            }).join('');
        }

        div.innerHTML = `
            <div class="parse-result">
                <h4>解析结果</h4>
                <div class="parse-item"><span class="parse-label">Checkpoint</span><span class="parse-value">${data.checkpoint || '未知'}</span></div>
                <div class="parse-item"><span class="parse-label">本地仓库</span><span class="parse-value">${ckptStatus}</span></div>
                ${lorasHtml}
                <div class="parse-item"><span class="parse-label">采样器</span><span class="parse-value">${data.sampler}</span></div>
                <div class="parse-item"><span class="parse-label">步数 / CFG</span><span class="parse-value">${data.steps} / ${data.cfg}</span></div>
                <div class="parse-item"><span class="parse-label">尺寸</span><span class="parse-value">${data.width}×${data.height}</span></div>
                <div class="parse-item"><span class="parse-label">Seed</span><span class="parse-value">${data.seed}</span></div>
                ${data.prompt ? `<div style="margin-top:8px;"><strong style="font-size:13px;">Prompt:</strong><div class="prompt-preview">${this.escapeHtml(data.prompt)}</div></div>` : ''}
                ${data.negative_prompt ? `<div style="margin-top:8px;"><strong style="font-size:13px;">Negative:</strong><div class="prompt-preview">${this.escapeHtml(data.negative_prompt)}</div></div>` : ''}
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

        if (data.all_models_found) {
            autoBtn.style.display = 'block';
        } else {
            autoBtn.style.display = 'none';
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
        document.getElementById('autoGenerateBtn').style.display = 'none';
    }

    async autoGenerate() {
        if (!this.parsedData || !this.parsedData.all_models_found) return;

        const btn = document.getElementById('autoGenerateBtn');
        this.setBtnLoading(btn, true);

        const d = this.parsedData;
        const sourceUrl = document.getElementById('imageUrl').value.trim();
        const ckpt = d.checks.checkpoint;
        // 构建 LoRA 列表（从解析结果中取已找到的 LoRA）
        const loraList = (d.checks.loras || [])
            .filter(lc => lc.found)
            .map(lc => ({ name: `${lc.subtype}/${lc.filename}`, weight: lc.weight || 1.0 }));

        const payload = {
            workflow: document.getElementById('autoWorkflowSelect').value || 'nolora',
            checkpoint: ckpt.found ? `${ckpt.subtype}/${ckpt.filename}` : '',
            prompt: d.prompt,
            negative_prompt: d.negative_prompt || 'low quality, worst quality',
            width: d.width, height: d.height,
            steps: d.steps || 20, cfg: d.cfg || 7, sampler: d.sampler || 'dpmpp_2m',
            seed: d.seed > 0 ? d.seed : null,
            loras: loraList.length > 0 ? loraList : undefined,
            vary_sizes: true
        };

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
        btn.textContent = '⏳ 重启中...';
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
                            btn.textContent = '🔄 重启';
                        }
                    } catch {}
                    if (attempts >= 40) {
                        clearInterval(waitReady);
                        badge.className = 'status-badge error';
                        text.textContent = '重启超时，请检查 watchdog';
                        btn.disabled = false;
                        btn.textContent = '🔄 重启';
                    }
                }, 3000);
            } else {
                text.textContent = data.message || '重启失败';
                btn.disabled = false;
                btn.textContent = '🔄 重启';
            }
        } catch (err) {
            badge.className = 'status-badge error';
            text.textContent = '重启请求失败';
            btn.disabled = false;
            btn.textContent = '🔄 重启';
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
}

document.addEventListener('DOMContentLoaded', () => new CivitaiApp());
