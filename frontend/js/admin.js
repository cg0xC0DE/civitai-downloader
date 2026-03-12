/**
 * Admin: Model Index Editor
 */
class AdminModelIndex {
    constructor(apiBase) {
        this.api = apiBase;
        this.items = [];
        this.diskFiles = { ckpt: [], lora: [], embedding: [] };
        this.init();
    }

    init() {
        document.getElementById('adminSearchBtn').addEventListener('click', () => this.loadIndex());
        document.getElementById('adminSearchInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.loadIndex();
        });
        document.getElementById('adminAddBtn').addEventListener('click', () => this.showAddForm());
        document.getElementById('adminRefreshBtn').addEventListener('click', () => this.loadIndex());
    }

    async apiFetch(url, options = {}) {
        options.headers = Object.assign({ 'ngrok-skip-browser-warning': '1' }, options.headers || {});
        return fetch(url, options);
    }

    async loadIndex() {
        const q = document.getElementById('adminSearchInput').value.trim();
        const container = document.getElementById('adminIndexList');
        container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);"><span class="loading-spinner"></span> 加载中...</div>';
        try {
            const res = await this.apiFetch(`${this.api}/api/admin/model-index?q=${encodeURIComponent(q)}`);
            const data = await res.json();
            this.items = data.items || [];
            this.renderList();
        } catch (e) {
            container.innerHTML = `<div style="color:#dc2626;padding:12px;">加载失败: ${e.message}</div>`;
        }
    }

    renderList() {
        const container = document.getElementById('adminIndexList');
        if (!this.items.length) {
            container.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-secondary);">无匹配结果</div>';
            return;
        }
        container.innerHTML = this.items.map(m => {
            const versions = (m.versions || []).map(v => {
                const statusIcon = v.exists ? '✅' : '❌';
                const sizeInfo = v.exists ? '' : ' <span style="color:#dc2626;font-size:11px;">(文件不存在)</span>';
                return `
                <div class="admin-version" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);">
                    <span style="flex-shrink:0;">${statusIcon}</span>
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:12px;font-weight:500;">${this._esc(v.version_name)} <span style="color:var(--text-secondary);font-size:11px;">vid:${v.version_id}</span></div>
                        <div style="font-size:11px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${this._esc(v.filename)}">${this._esc(v.filename)}</div>
                        ${v.trigger_words && v.trigger_words.length ? `<div style="font-size:11px;color:#a78bfa;">触发词: ${this._esc(v.trigger_words.join(', '))}</div>` : ''}
                    </div>
                    <button class="admin-btn admin-btn-edit" onclick="adminIndex.editVersion('${m.model_id}','${v.version_id}')" title="编辑">✏️</button>
                    <button class="admin-btn admin-btn-del" onclick="adminIndex.deleteVersion('${m.model_id}','${v.version_id}','${this._esc(v.version_name)}')" title="删除">🗑️</button>
                </div>`;
            }).join('');
            return `
            <div class="admin-model-card">
                <div class="admin-model-header">
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:14px;font-weight:600;">${this._esc(m.name)}</div>
                        <div style="font-size:11px;color:var(--text-secondary);">model_id: ${m.model_id} · ${m.versions.length} 个版本</div>
                    </div>
                    <button class="admin-btn admin-btn-add-ver" onclick="adminIndex.showAddVersionForm('${m.model_id}','${this._esc(m.name)}')" title="添加版本">➕</button>
                </div>
                <div class="admin-versions">${versions}</div>
            </div>`;
        }).join('');
    }

    async showAddForm() {
        const html = this._buildForm({}, '新增模型映射');
        this._showModal(html);
    }

    async showAddVersionForm(modelId, modelName) {
        const html = this._buildForm({ model_id: modelId, model_name: modelName }, `为 ${modelName} 添加版本`);
        this._showModal(html);
    }

    async editVersion(modelId, versionId) {
        const model = this.items.find(m => m.model_id === modelId);
        if (!model) return;
        const ver = model.versions.find(v => v.version_id === versionId);
        if (!ver) return;
        const html = this._buildForm({
            model_id: modelId, model_name: model.name,
            version_id: versionId, version_name: ver.version_name,
            filename: ver.filename, path: ver.path,
            trigger_words: (ver.trigger_words || []).join(', ')
        }, `编辑: ${model.name} / ${ver.version_name}`);
        this._showModal(html);
    }

    _buildForm(data, title) {
        const ro = data.model_id ? 'readonly style="opacity:0.6;"' : '';
        return `
        <div class="admin-modal-title">${title}</div>
        <div class="admin-form">
            <label>Model ID <input type="text" id="af_model_id" value="${data.model_id || ''}" ${ro} placeholder="Civitai model ID"></label>
            <label>模型名称 <input type="text" id="af_model_name" value="${this._esc(data.model_name || '')}" placeholder="如: JANKU Trained..."></label>
            <label>Version ID <input type="text" id="af_version_id" value="${data.version_id || ''}" placeholder="Civitai version ID"></label>
            <label>版本名称 <input type="text" id="af_version_name" value="${this._esc(data.version_name || '')}" placeholder="如: v6.9"></label>
            <label>文件路径
                <div style="display:flex;gap:6px;">
                    <input type="text" id="af_path" value="${this._esc(data.path || '')}" placeholder="D:\\ckpt\\xl\\xxx.safetensors" style="flex:1;">
                    <button type="button" class="admin-btn-pick" onclick="adminIndex.showFilePicker()">📂 选择</button>
                </div>
            </label>
            <label>文件名 <input type="text" id="af_filename" value="${this._esc(data.filename || '')}" placeholder="留空则自动从路径提取"></label>
            <label>触发词 <input type="text" id="af_trigger" value="${this._esc(data.trigger_words || '')}" placeholder="逗号分隔，如: tag1, tag2"></label>
            <div style="display:flex;gap:8px;margin-top:12px;">
                <button class="btn btn-primary" style="flex:1;" onclick="adminIndex.submitForm()">💾 保存</button>
                <button class="btn btn-outline" style="flex:1;" onclick="adminIndex.closeModal()">取消</button>
            </div>
        </div>
        <div id="af_picker" style="display:none;"></div>`;
    }

    async showFilePicker() {
        const picker = document.getElementById('af_picker');
        picker.style.display = 'block';
        picker.innerHTML = '<div style="text-align:center;padding:12px;"><span class="loading-spinner"></span> 扫描磁盘...</div>';
        // Load all types
        const types = ['ckpt', 'lora'];
        const allFiles = [];
        for (const t of types) {
            try {
                const res = await this.apiFetch(`${this.api}/api/admin/disk-files?type=${t}`);
                const data = await res.json();
                (data.files || []).forEach(f => { f.type = t; allFiles.push(f); });
            } catch (e) { /* ignore */ }
        }
        if (!allFiles.length) {
            picker.innerHTML = '<div style="color:var(--text-secondary);padding:8px;">未找到模型文件</div>';
            return;
        }
        picker.innerHTML = `
            <div style="margin:8px 0;"><input type="text" id="af_picker_search" placeholder="搜索文件名..." style="width:100%;padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);font-size:12px;outline:none;"></div>
            <div id="af_picker_list" style="max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;"></div>`;
        this._allDiskFiles = allFiles;
        this._renderPickerList('');
        document.getElementById('af_picker_search').addEventListener('input', (e) => {
            this._renderPickerList(e.target.value.toLowerCase());
        });
    }

    _renderPickerList(filter) {
        const list = document.getElementById('af_picker_list');
        const filtered = this._allDiskFiles.filter(f =>
            !filter || f.filename.toLowerCase().includes(filter) || f.subtype.toLowerCase().includes(filter)
        ).slice(0, 50);
        if (!filtered.length) {
            list.innerHTML = '<div style="padding:8px;color:var(--text-secondary);font-size:12px;">无匹配</div>';
            return;
        }
        list.innerHTML = filtered.map(f => `
            <div class="admin-picker-item" onclick="adminIndex.pickFile(this)" data-path="${this._esc(f.path)}" data-filename="${this._esc(f.filename)}">
                <span style="color:var(--text-secondary);font-size:11px;">[${f.type}/${f.subtype}]</span>
                <span style="font-size:12px;">${this._esc(f.filename)}</span>
                <span style="color:var(--text-secondary);font-size:11px;margin-left:auto;">${f.size_mb}MB</span>
            </div>
        `).join('');
    }

    pickFile(el) {
        document.getElementById('af_path').value = el.dataset.path;
        document.getElementById('af_filename').value = el.dataset.filename;
        document.getElementById('af_picker').style.display = 'none';
    }

    async submitForm() {
        const model_id = document.getElementById('af_model_id').value.trim();
        const model_name = document.getElementById('af_model_name').value.trim();
        const version_id = document.getElementById('af_version_id').value.trim();
        const version_name = document.getElementById('af_version_name').value.trim();
        const path = document.getElementById('af_path').value.trim();
        const filename = document.getElementById('af_filename').value.trim();
        const triggerStr = document.getElementById('af_trigger').value.trim();
        const trigger_words = triggerStr ? triggerStr.split(',').map(s => s.trim()).filter(Boolean) : [];

        if (!model_id || !version_id) { showToast('Model ID 和 Version ID 必填', 'error'); return; }
        if (!path) { showToast('文件路径必填', 'error'); return; }

        try {
            const res = await this.apiFetch(`${this.api}/api/admin/model-index/upsert`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_id, model_name, version_id, version_name, path, filename, trigger_words })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                showToast(data.message || '保存成功');
                this.closeModal();
                this.loadIndex();
            } else {
                showToast(data.message || '保存失败', 'error');
            }
        } catch (e) {
            showToast('请求失败: ' + e.message, 'error');
        }
    }

    async deleteVersion(modelId, versionId, versionName) {
        if (!confirm(`确认删除版本 ${versionName} (${versionId})？`)) return;
        try {
            const res = await this.apiFetch(`${this.api}/api/admin/model-index/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_id: modelId, version_id: versionId })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                showToast(data.message || '已删除');
                this.loadIndex();
            } else {
                showToast(data.message || '删除失败', 'error');
            }
        } catch (e) {
            showToast('请求失败: ' + e.message, 'error');
        }
    }

    _showModal(html) {
        let overlay = document.getElementById('adminModalOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'adminModalOverlay';
            overlay.className = 'admin-modal-overlay';
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.closeModal(); });
            document.body.appendChild(overlay);
        }
        overlay.innerHTML = `<div class="admin-modal">${html}</div>`;
        overlay.style.display = 'flex';
    }

    closeModal() {
        const overlay = document.getElementById('adminModalOverlay');
        if (overlay) overlay.style.display = 'none';
    }

    _esc(s) {
        if (!s) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
}

let adminIndex;
document.addEventListener('DOMContentLoaded', () => {
    // Reuse same API base detection logic as CivitaiApp
    const loc = window.location;
    let api;
    if (loc.pathname.startsWith('/civitaidl') || loc.hostname.includes('ngrok')) {
        api = loc.origin + '/civitaidl-service';
    } else {
        api = loc.protocol + '//' + loc.hostname + ':53133';
    }
    adminIndex = new AdminModelIndex(api);

    // Auto-load index when admin tab is first activated
    let adminLoaded = false;
    const adminTab = document.querySelector('.tab-btn[data-tab="admin"]');
    if (adminTab) {
        adminTab.addEventListener('click', () => {
            if (!adminLoaded) { adminIndex.loadIndex(); adminLoaded = true; }
        });
    }

    // ---- Admin 子 Tab 切换 ----
    document.querySelectorAll('.admin-sub-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.admin-sub-tab').forEach(b => {
                b.style.background = 'var(--bg-secondary)';
                b.style.color = 'var(--text-secondary)';
                b.classList.remove('active');
            });
            btn.style.background = 'var(--primary)';
            btn.style.color = '#fff';
            btn.classList.add('active');
            document.querySelectorAll('.admin-sub-content').forEach(c => c.style.display = 'none');
            const target = document.getElementById(btn.dataset.sub);
            if (target) target.style.display = '';
        });
    });

    // ---- 手动注册表单 ----
    const mrBtn = document.getElementById('mrSubmitBtn');
    if (mrBtn) {
        mrBtn.addEventListener('click', async () => {
            const civitaiUrl = document.getElementById('mrCivitaiUrl').value.trim();
            const localFilename = document.getElementById('mrLocalFilename').value.trim();
            const typeSubtype = document.getElementById('mrTypeSubtype').value;
            const aliasName = document.getElementById('mrAliasName').value.trim();
            const resultDiv = document.getElementById('mrResult');

            if (!civitaiUrl || !localFilename) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#dc262620';
                resultDiv.style.color = '#f87171';
                resultDiv.textContent = '请填写 Civitai URL 和本地文件名';
                return;
            }

            mrBtn.disabled = true;
            mrBtn.textContent = '⏳ 注册中...';
            resultDiv.style.display = 'none';

            try {
                const res = await fetch(`${api}/api/admin/manual-register`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
                    body: JSON.stringify({
                        civitai_url: civitaiUrl,
                        local_filename: localFilename,
                        type_subtype: typeSubtype,
                        alias_name: aliasName,
                    })
                });
                const data = await res.json();
                resultDiv.style.display = 'block';
                if (data.status === 'ok') {
                    resultDiv.style.background = '#22c55e20';
                    resultDiv.style.color = '#4ade80';
                    let html = `<strong>✅ ${data.message}</strong><br>`;
                    html += `文件名: ${data.new_filename}${data.renamed ? ' (已重命名)' : ''}<br>`;
                    html += `Model ID: ${data.model_id} · Version ID: ${data.version_id}<br>`;
                    if (data.alias_version_id) html += `别名 Version ID: ${data.alias_version_id}<br>`;
                    html += `Base Model: ${data.base_model || '-'}<br>`;
                    if (data.trained_words && data.trained_words.length) {
                        html += `触发词: <span style="color:#a78bfa;">${data.trained_words.join(', ')}</span>`;
                    }
                    resultDiv.innerHTML = html;
                } else {
                    resultDiv.style.background = '#dc262620';
                    resultDiv.style.color = '#f87171';
                    resultDiv.textContent = `❌ ${data.message || '注册失败'}`;
                }
            } catch (e) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = '#dc262620';
                resultDiv.style.color = '#f87171';
                resultDiv.textContent = `❌ 请求失败: ${e.message}`;
            }
            mrBtn.disabled = false;
            mrBtn.textContent = '📦 注册模型';
        });
    }
});
