/* pages/memory.js - memory list and conflict resolver */

let memPage = 1, memSearch = '', memTab = 'list';

const memEscapeHtml = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

document.addEventListener('click', async e => {
  const editBtn = e.target.closest('.mem-edit');
  const delBtn = e.target.closest('.mem-del');

  if (editBtn) {
    const id = editBtn.dataset.id;
    const current = editBtn.closest('.memory-card')?.querySelector('.memory-entry')?.textContent || '';
    const entry = await dialogPrompt('编辑记忆内容', current);
    if (entry === null) return;
    try {
      await api(`/api/memory/${id}`, { method: 'PUT', body: JSON.stringify({ entry }) });
      toast('已更新', 'ok');
      navigate('memory');
    } catch (err) { toast(err.message, 'err'); }
  }

  if (delBtn) {
    const id = delBtn.dataset.id;
    const ok = await dialogConfirm('确定删除这条记忆？');
    if (!ok) return;
    try {
      await api(`/api/memory/${id}`, { method: 'DELETE' });
      toast('已删除', 'ok');
      navigate('memory');
    } catch (err) { toast(err.message, 'err'); }
  }
});

function renderMemoryCard(m, options = {}) {
  const selectable = options.selectable;
  const checked = options.checked;
  return `
    <div class="memory-card" data-id="${m.id}">
      <div class="memory-meta">
        ${selectable ? `<label class="memory-check"><input type="${options.radio ? 'radio' : 'checkbox'}" name="${options.name || 'mem-select'}" value="${m.id}" ${checked ? 'checked' : ''}> ${options.radio ? '保留' : '废弃'}</label>` : ''}
        <span class="badge badge-neutral">${memEscapeHtml(m.category || '-')}</span>
        ${m.importance ? `<span class="badge ${m.importance === 'high' ? 'badge-err' : m.importance === 'medium' ? 'badge-warn' : 'badge-neutral'}">${memEscapeHtml(m.importance)}</span>` : ''}
        ${m.emotion ? `<span class="badge badge-neutral">${memEscapeHtml(m.emotion)}</span>` : ''}
      </div>
      <div class="memory-entry">${memEscapeHtml(m.entry || '')}</div>
      <div class="memory-footer">
        <span>${memEscapeHtml(m.created_at || '')}${m.source_msg ? ' · ' + memEscapeHtml(m.source_msg) : ''}</span>
        ${options.actions === false ? '' : `
          <div class="btn-group">
            <button class="btn btn-sm mem-edit" data-id="${m.id}">编辑</button>
            <button class="btn btn-sm btn-danger mem-del" data-id="${m.id}">删除</button>
          </div>
        `}
      </div>
    </div>
  `;
}

window.Pages.memory = async function(el) {
  const params = new URLSearchParams({ page: memPage, page_size: 20 });
  if (memSearch) params.set('search', memSearch);

  let data = null;
  let conflictData = null;
  try { data = await api(`/api/memory/list?${params}`); } catch (_) {}
  try { conflictData = await api('/api/memory/conflicts?limit=30'); } catch (_) { conflictData = { conflicts: [] }; }

  const memories = data ? data.memories : [];
  const total = data ? data.total : 0;
  const totalPages = data ? data.total_pages : 1;
  const conflicts = conflictData?.conflicts || [];

  const renderList = () => `
    <div class="filter-bar">
      <input type="search" id="mem-search" placeholder="搜索记忆..." value="${memEscapeHtml(memSearch)}">
      <button class="btn btn-sm" id="btn-mem-search">搜索</button>
      <button class="btn btn-sm" id="btn-mem-reset">重置</button>
      <span style="flex:1;min-width:12px"></span>
      <button class="btn btn-sm" id="btn-mem-export">导出 JSON</button>
      <button class="btn btn-sm btn-danger" id="btn-mem-clear">清空全部</button>
    </div>

    <div id="mem-list">
      ${memories.length === 0 ? '<div class="empty">暂无记忆</div>' : memories.map(m => renderMemoryCard(m)).join('')}
    </div>

    ${totalPages > 1 ? `
      <div class="pagination">
        <button class="btn btn-sm" id="btn-mem-prev" ${memPage <= 1 ? 'disabled' : ''}>上一页</button>
        <span>${memPage} / ${totalPages}（共 ${total} 条）</span>
        <button class="btn btn-sm" id="btn-mem-next" ${memPage >= totalPages ? 'disabled' : ''}>下一页</button>
      </div>` : ''}
  `;

  const renderConflicts = () => `
    <div class="card">
      <h3>疑似冲突/状态变化</h3>
      <p class="form-hint">这里不会自动删除记忆，只把“感冒了 → 好了”这类状态变化列出来，由你选择保留、合并或废弃。</p>
    </div>
    ${conflicts.length === 0 ? '<div class="empty">暂未发现明显冲突记忆</div>' : conflicts.map(group => {
      const suggested = String(group.suggested_keep_id || '');
      const newest = group.memories?.[0] || {};
      return `
        <div class="card memory-conflict-card" data-conflict="${memEscapeHtml(group.id)}">
          <div class="card-head">
            <div>
              <h3>冲突候选</h3>
              <p class="form-hint">${memEscapeHtml(group.reason || '疑似状态变化')}</p>
            </div>
            <span class="badge badge-warn">建议保留 #${memEscapeHtml(suggested)}</span>
          </div>
          <div class="memory-conflict-grid">
            ${(group.memories || []).map(m => renderMemoryCard(m, {
              selectable: true,
              radio: true,
              name: `keep-${group.id}`,
              checked: String(m.id) === suggested,
              actions: false,
            })).join('')}
          </div>
          <div class="form-group">
            <label>合并后的记忆内容</label>
            <textarea class="conflict-merged-entry" rows="3">${memEscapeHtml(newest.entry || '')}</textarea>
          </div>
          <div class="btn-group">
            <button class="btn btn-primary conflict-merge" data-id="${memEscapeHtml(group.id)}">保留选中并废弃其它</button>
            <button class="btn conflict-keep-both" data-id="${memEscapeHtml(group.id)}">暂不处理</button>
          </div>
        </div>
      `;
    }).join('')}
  `;

  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>记忆管理</h2>
        <p class="page-subtitle">查看、编辑、导出记忆，并可视化处理状态变化。</p>
      </div>
    </div>
    <div class="tabs">
      <button class="tab ${memTab === 'list' ? 'active' : ''}" data-mem-tab="list">全部记忆</button>
      <button class="tab ${memTab === 'conflicts' ? 'active' : ''}" data-mem-tab="conflicts">冲突处理 ${conflicts.length ? `(${conflicts.length})` : ''}</button>
    </div>
    ${memTab === 'conflicts' ? renderConflicts() : renderList()}
  `;

  document.querySelectorAll('[data-mem-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      memTab = btn.dataset.memTab;
      navigate('memory');
    });
  });

  document.getElementById('btn-mem-search')?.addEventListener('click', () => {
    memSearch = document.getElementById('mem-search').value;
    memPage = 1;
    navigate('memory');
  });
  document.getElementById('mem-search')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('btn-mem-search')?.click();
  });
  document.getElementById('btn-mem-reset')?.addEventListener('click', () => {
    memSearch = ''; memPage = 1; navigate('memory');
  });
  document.getElementById('btn-mem-prev')?.addEventListener('click', () => { memPage--; navigate('memory'); });
  document.getElementById('btn-mem-next')?.addEventListener('click', () => { memPage++; navigate('memory'); });

  document.getElementById('btn-mem-export')?.addEventListener('click', async () => {
    try {
      const exported = await api('/api/memory/export');
      const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'memories.json';
      a.click();
      URL.revokeObjectURL(url);
      toast('导出成功', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });

  document.getElementById('btn-mem-clear')?.addEventListener('click', async () => {
    const ok = await dialogConfirm('确定清空所有记忆？此操作不可撤销。');
    if (!ok) return;
    try {
      await api('/api/memory/clear', { method: 'POST' });
      toast('已清空', 'ok');
      navigate('memory');
    } catch (e) { toast(e.message, 'err'); }
  });

  document.querySelectorAll('.conflict-merge').forEach(btn => {
    btn.addEventListener('click', async function() {
      const card = this.closest('.memory-conflict-card');
      const keep = card.querySelector('input[type="radio"]:checked')?.value;
      const allIds = [...card.querySelectorAll('input[type="radio"]')].map(x => Number(x.value));
      const discardIds = allIds.filter(id => String(id) !== String(keep));
      const mergedEntry = card.querySelector('.conflict-merged-entry')?.value || '';
      if (!keep) return toast('请选择要保留的记忆', 'err');
      const ok = await dialogConfirm('确认合并？选中的记忆会更新为合并内容，其它候选会被废弃。');
      if (!ok) return;
      try {
        await api('/api/memory/resolve-conflict', {
          method: 'POST',
          body: JSON.stringify({ keep_id: Number(keep), discard_ids: discardIds, merged_entry: mergedEntry }),
        });
        toast('冲突已处理', 'ok');
        navigate('memory');
      } catch (e) { toast(e.message, 'err'); }
    });
  });

  document.querySelectorAll('.conflict-keep-both').forEach(btn => {
    btn.addEventListener('click', () => toast('已暂不处理，本次不会改动数据库', 'ok'));
  });
};
