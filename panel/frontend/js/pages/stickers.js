/* pages/stickers.js - UTF-8 sticker visual management */

window.Pages.stickers = async function(el) {
  let stickers;
  let logs;
  try { stickers = await api('/api/settings/stickers'); } catch (_) { stickers = null; }
  try { logs = await api('/api/settings/stickers/logs?limit=120'); } catch (_) { logs = { logs: [] }; }

  const escapeHtml = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  const PREVIEW_LIMIT = 8;
  const assets = stickers?.assets || [];
  const summary = stickers?.intents_summary || [];
  let config = {};
  try { config = JSON.parse(stickers?.config_text || '{}'); } catch (_) { config = {}; }
  const intentSet = new Set([
    ...summary.map(row => row.intent),
    ...assets.map(row => row.intent),
    ...Object.keys(config.intents || {}),
  ].filter(Boolean));
  const intents = [...intentSet].sort();
  const INTENT_LABELS = {
    happy: '开心',
    comfort: '安慰',
    hug: '抱抱',
    shy: '害羞',
    angry_cute: '可爱生气',
    goodnight: '晚安',
    food: '吃饭',
    miss_you: '想你',
  };
  const displayIntent = (intent) => {
    const raw = String(intent || '');
    const label = INTENT_LABELS[raw];
    return label ? `${raw}（${label}）` : raw;
  };
  const intentOptions = (selected) => intents.map(intent => `<option value="${escapeHtml(intent)}" ${intent === selected ? 'selected' : ''}>${escapeHtml(displayIntent(intent))}</option>`).join('');
  const refreshStickers = async () => {
    await window.Pages.stickers(el);
  };

  const renderThumb = (item) => `
    <button class="sticker-thumb ${item.exists ? '' : 'missing'}" data-path="${escapeHtml(item.path)}" title="${escapeHtml(item.path || '')}">
      ${item.preview_data_url ? `<img src="${item.preview_data_url}" alt="${escapeHtml(item.intent)}">` : `<span>${item.exists ? '无预览' : '缺图'}</span>`}
    </button>
  `;

  const renderAssetDetail = (item) => `
    <div class="sticker-detail-card ${item.exists ? '' : 'missing'}" data-path="${escapeHtml(item.path)}">
      <div class="sticker-detail-img">
        ${item.preview_data_url ? `<img src="${item.preview_data_url}" alt="${escapeHtml(item.intent)}">` : `<div class="sticker-empty">${item.exists ? '无预览' : '缺图'}</div>`}
      </div>
      <div class="sticker-detail-body">
        <div class="sticker-meta" title="${escapeHtml(item.path)}">${escapeHtml(item.path || '-')}</div>
        <label class="sticker-weight-row">权重 <input class="sticker-weight" type="number" min="1" max="20" value="${Number(item.weight || 1)}" data-path="${escapeHtml(item.path)}"></label>
        <div class="sticker-action-row">
          <button class="btn btn-sm sticker-move-toggle" data-path="${escapeHtml(item.path)}">移动</button>
          <button class="btn btn-sm btn-danger sticker-delete" data-path="${escapeHtml(item.path)}">删除</button>
        </div>
        <div class="sticker-move-panel" data-path="${escapeHtml(item.path)}" hidden>
          <label>移动到</label>
          <select class="sticker-move-select" data-path="${escapeHtml(item.path)}">${intentOptions(item.intent)}</select>
          <button class="btn btn-sm btn-primary sticker-move-confirm" data-path="${escapeHtml(item.path)}">确认移动</button>
        </div>
      </div>
    </div>
  `;

  const renderIntentCard = (intent) => {
    const row = summary.find(x => x.intent === intent) || { count: 0, missing: 0 };
    const items = assets.filter(x => x.intent === intent && x.exists);
    const visibleItems = items.slice(0, PREVIEW_LIMIT);
    const hiddenCount = Math.max(0, items.length - visibleItems.length);
    return `
      <div class="sticker-intent-card">
        <div class="sticker-intent-head">
          <div class="sticker-intent-title">
            <h4>${escapeHtml(displayIntent(intent))}</h4>
            <p>${items.filter(item => item.exists).length} 张图片</p>
          </div>
          <button class="sticker-icon-btn sticker-rename-intent" data-intent="${escapeHtml(intent)}" title="重命名分类" aria-label="重命名分类" style="width:32px;height:32px;flex:0 0 32px;padding:0;">
            <img src="assets/icons/sticker-edit.svg" alt="" width="16" height="16" style="width:16px;height:16px;display:block;">
          </button>
        </div>
        <div class="sticker-thumb-grid">
          ${visibleItems.length ? visibleItems.map(renderThumb).join('') : '<div class="empty compact">暂无图片</div>'}
          <label class="sticker-thumb sticker-upload-tile" title="上传表情包">
            <img src="assets/icons/sticker-add.svg" alt="" width="22" height="22" style="width:22px;height:22px;display:block;">
            <input class="sticker-upload" type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-intent="${escapeHtml(intent)}" hidden>
          </label>
          ${hiddenCount ? `<button class="sticker-more-tile" data-intent="${escapeHtml(intent)}">&hellip;<span>+${hiddenCount}</span></button>` : ''}
        </div>
      </div>
    `;
  };

  const renderGalleryModals = () => intents.map(intent => {
    const items = assets.filter(x => x.intent === intent && x.exists);
    return `
      <div class="sticker-gallery-overlay" data-intent="${escapeHtml(intent)}" hidden>
        <div class="sticker-gallery-card">
          <div class="card-head">
            <div>
              <h3>${escapeHtml(displayIntent(intent))}</h3>
              <p class="form-hint">${items.length} 张图片</p>
            </div>
            <button class="btn btn-sm sticker-gallery-close" data-intent="${escapeHtml(intent)}">关闭</button>
          </div>
          <div class="sticker-detail-grid">
            ${items.length ? items.map(renderAssetDetail).join('') : '<div class="empty compact">暂无图片</div>'}
          </div>
        </div>
      </div>
    `;
  }).join('');

  const renderLogs = () => {
    const rows = logs?.logs || [];
    if (!rows.length) return '<div class="empty compact">暂无调用记录</div>';
    return `
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>状态</th><th>Intent</th><th>平台/会话</th><th>路径</th></tr></thead>
          <tbody>
            ${rows.map(row => `
              <tr>
                <td class="mono">${escapeHtml(row.ts || '-')}</td>
                <td><span class="badge ${row.status === 'media_tag_generated' ? 'badge-ok' : row.status === 'cooldown' ? 'badge-warn' : 'badge-neutral'}">${escapeHtml(row.status || row.raw || '-')}</span></td>
                <td class="mono">${escapeHtml(row.intent || '-')}</td>
                <td class="mono">${escapeHtml(row.platform || '-')}/${escapeHtml(row.session_id || '-')}</td>
                <td class="mono">${escapeHtml(row.path || '-')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  };

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>表情包系统</h2>
        <p class="page-subtitle">管理本地表情包、权重、分类和实际调用记录。</p>
      </div>
    </div>

    <div class="settings-single-column">
      <div class="card">
        <h3>基础设置</h3>
        <div class="theme-toggle" style="margin:12px 0">
          <span>启用表情包</span>
          <label class="theme-switch">
            <input type="checkbox" id="set-sticker-enabled" ${!stickers || stickers.enabled ? 'checked' : ''}>
            <span class="theme-slider"></span>
          </label>
        </div>
        <div class="settings-inline-fields">
          <div class="form-group">
            <label>冷却时间（秒）</label>
            <input id="set-sticker-cooldown" type="number" min="0" max="86400" step="10" value="${stickers ? stickers.cooldown_seconds || 600 : 600}">
          </div>
          <div class="form-group">
            <label>每轮最多发送</label>
            <input id="set-sticker-max" type="number" min="0" max="3" step="1" value="${stickers ? stickers.max_per_turn || 1 : 1}">
          </div>
          <div class="form-group">
            <label>启用平台</label>
            <input id="set-sticker-platforms" value="${escapeHtml((stickers?.inject_only_for_platforms || ['weixin']).join(', '))}" placeholder="weixin">
          </div>
        </div>
        <div class="btn-group">
          ${stickers?.installed ? '' : '<button class="btn" id="btn-install-stickers">安装表情包插件</button>'}
          <button class="btn btn-primary" id="btn-save-stickers">保存基础设置</button>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>素材管理</h3>
          <button class="btn btn-sm" id="btn-new-intent">新增分类</button>
        </div>
        <p class="form-hint">分类改名只改 intent 归类；单张图片的“移动”使用下拉框选择目标分类。</p>
      </div>
      <div class="sticker-intent-grid">
        ${intents.map(renderIntentCard).join('') || '<div class="empty">暂无表情包分类，先新增一个分类。</div>'}
      </div>

      <div class="card">
        <div class="card-head">
          <h3>调用记录</h3>
          <button class="btn btn-sm" id="btn-refresh-sticker-logs">刷新</button>
        </div>
        ${renderLogs()}
      </div>

      <div class="card">
        <details class="settings-details">
          <summary>高级：查看 stickers.json</summary>
          <textarea id="set-sticker-json" rows="14" spellcheck="false">${escapeHtml(stickers?.config_text || '')}</textarea>
        </details>
      </div>
    </div>
    ${renderGalleryModals()}
  `;

  async function saveWithRestart(btn, apiCall, label, successMsg) {
    try {
      btn.disabled = true;
      btn.textContent = '保存中...';
      await apiCall();
      btn.textContent = '重启中...';
      showRestartOverlay('容器重启中，约 5-10 秒...');
      await waitForRestart();
      hideRestartOverlay();
      toast(successMsg, 'ok');
      navigate('stickers');
    } catch (e) {
      hideRestartOverlay();
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  const readFileAsDataUrl = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error('read failed'));
    reader.readAsDataURL(file);
  });

  document.getElementById('btn-install-stickers')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-install-stickers');
    saveWithRestart(btn, () => api('/api/settings/stickers/install', { method: 'POST' }), '安装表情包插件', '表情包插件已安装');
  });

  document.getElementById('btn-save-stickers')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-save-stickers');
    saveWithRestart(btn, async () => {
      const rawJson = document.getElementById('set-sticker-json')?.value.trim();
      const body = {
        enabled: document.getElementById('set-sticker-enabled').checked,
        cooldown_seconds: Number(document.getElementById('set-sticker-cooldown').value) || 0,
        max_per_turn: Number(document.getElementById('set-sticker-max').value) || 0,
        inject_only_for_platforms: document.getElementById('set-sticker-platforms').value.split(',').map(x => x.trim()).filter(Boolean),
      };
      if (rawJson) {
        const parsed = JSON.parse(rawJson);
        parsed.enabled = body.enabled;
        parsed.cooldown_seconds = body.cooldown_seconds;
        parsed.max_per_turn = body.max_per_turn;
        parsed.inject_only_for_platforms = body.inject_only_for_platforms;
        body.config = parsed;
      }
      await api('/api/settings/stickers', { method: 'POST', body: JSON.stringify(body) });
    }, '保存基础设置', '表情包设置已保存');
  });

  document.getElementById('btn-new-intent')?.addEventListener('click', async () => {
    const intent = await dialogPrompt('输入新分类 intent，例如 happy / comfort / shy', '');
    if (!intent) return;
    const nextConfig = JSON.parse(stickers?.config_text || '{}');
    nextConfig.intents = nextConfig.intents || {};
    nextConfig.intents[intent] = nextConfig.intents[intent] || [];
    await api('/api/settings/stickers', { method: 'POST', body: JSON.stringify({ config: nextConfig }) });
    toast('分类已创建', 'ok');
    await refreshStickers();
  });

  document.querySelectorAll('.sticker-upload').forEach(input => {
    input.addEventListener('change', async function() {
      const file = this.files && this.files[0];
      if (!file) return;
      try {
        const dataUrl = await readFileAsDataUrl(file);
        await api('/api/settings/stickers/upload', {
          method: 'POST',
          body: JSON.stringify({ intent: this.dataset.intent, filename: file.name, data_url: dataUrl, weight: 1 }),
        });
        toast('表情包已上传', 'ok');
        this.value = '';
        await refreshStickers();
      } catch (e) {
        toast(e.message || '上传失败', 'err');
      }
    });
  });

  document.querySelectorAll('.sticker-open-gallery, .sticker-more-tile, .sticker-thumb').forEach(btn => {
    btn.addEventListener('click', function() {
      const intent = this.dataset.intent || assets.find(x => x.path === this.dataset.path)?.intent;
      const modal = document.querySelector(`.sticker-gallery-overlay[data-intent="${CSS.escape(intent || '')}"]`);
      if (modal) modal.hidden = false;
    });
  });

  document.querySelectorAll('.sticker-gallery-close, .sticker-gallery-overlay').forEach(node => {
    node.addEventListener('click', function(e) {
      if (e.target === this || e.target.classList.contains('sticker-gallery-close')) {
        const modal = e.target.closest('.sticker-gallery-overlay') || this;
        modal.hidden = true;
      }
    });
  });

  document.querySelectorAll('.sticker-weight').forEach(input => {
    input.addEventListener('change', async function() {
      try {
        await api(`/api/settings/stickers/assets?path=${encodeURIComponent(this.dataset.path)}`, {
          method: 'PATCH',
          body: JSON.stringify({ weight: Number(this.value) || 1 }),
        });
        toast('权重已更新', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    });
  });

  document.querySelectorAll('.sticker-move-toggle').forEach(btn => {
    btn.addEventListener('click', function() {
      const panel = document.querySelector(`.sticker-move-panel[data-path="${CSS.escape(this.dataset.path)}"]`);
      if (panel) panel.hidden = !panel.hidden;
    });
  });

  document.querySelectorAll('.sticker-move-confirm').forEach(btn => {
    btn.addEventListener('click', async function() {
      const select = document.querySelector(`.sticker-move-select[data-path="${CSS.escape(this.dataset.path)}"]`);
      const intent = select?.value;
      if (!intent) return;
      try {
        await api(`/api/settings/stickers/assets?path=${encodeURIComponent(this.dataset.path)}`, {
          method: 'PATCH',
          body: JSON.stringify({ intent }),
        });
        toast('分类已更新', 'ok');
        await refreshStickers();
      } catch (e) { toast(e.message, 'err'); }
    });
  });

  document.querySelectorAll('.sticker-delete').forEach(btn => {
    btn.addEventListener('click', async function() {
      const ok = await dialogConfirm('确定删除这张表情包？会同时删除文件和配置引用。');
      if (!ok) return;
      try {
        await api(`/api/settings/stickers/assets?path=${encodeURIComponent(this.dataset.path)}`, { method: 'DELETE' });
        toast('表情包已删除', 'ok');
        await refreshStickers();
      } catch (e) { toast(e.message, 'err'); }
    });
  });

  document.querySelectorAll('.sticker-rename-intent').forEach(btn => {
    btn.addEventListener('click', async function() {
      const oldIntent = this.dataset.intent;
      const newIntent = await dialogPrompt('输入新的分类 intent', oldIntent);
      if (!newIntent || newIntent === oldIntent) return;
      try {
        await api(`/api/settings/stickers/intents/${encodeURIComponent(oldIntent)}/rename`, {
          method: 'POST',
          body: JSON.stringify({ new_intent: newIntent }),
        });
        toast('分类已重命名', 'ok');
        await refreshStickers();
      } catch (e) { toast(e.message, 'err'); }
    });
  });

  document.getElementById('btn-refresh-sticker-logs')?.addEventListener('click', () => navigate('stickers'));
};
