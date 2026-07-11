/* ── pages/container.js ──
   v5.0 — 容器管理。管理员可查看全部容器+操作自己容器，普通用户只读状态 */

window.Pages.container = async function(el) {
  if (isAdmin()) {
    await renderAdminContainers(el);
  } else {
    await renderUserContainer(el);
  }
};

async function renderUserContainer(el) {
  let status;
  try { status = await api('/api/container/status'); } catch (_) { status = null; }

  const s = status;
  const hasContainer = s && s.status !== 'not_created';
  const running = s && s.status === 'running';

  el.innerHTML = `
    <h2>容器管理</h2>

    <div class="card">
      <div class="card-row">
        <span class="card-label">状态</span>
        <span class="badge ${running ? 'badge-ok' : s && s.status === 'stopped' ? 'badge-warn' : 'badge-neutral'}">
          ${s ? s.status : '未知'}
        </span>
      </div>
      ${s && s.name ? `<div class="card-row"><span class="card-label">名称</span><span class="card-value mono">${escapeHtml(s.name)}</span></div>` : ''}
    </div>

    ${!hasContainer ? `
    <div class="card">
      <div class="empty">容器正在自动创建，请稍后刷新</div>
    </div>` : `
    <div class="btn-group" style="margin-bottom:16px">
      <button class="btn btn-sm" id="btn-refresh">刷新</button>
      <button class="btn btn-sm" id="btn-logs">查看日志</button>
    </div>
    <div id="logs-area" style="display:none">
      <h3>容器日志</h3>
      <div class="log-view" id="logs-content">加载中...</div>
    </div>`}
  `;

  if (!hasContainer) return;
  document.getElementById('btn-refresh')?.addEventListener('click', () => navigate('container'));

  document.getElementById('btn-logs')?.addEventListener('click', async () => {
    const area = document.getElementById('logs-area');
    const content = document.getElementById('logs-content');
    if (area.style.display !== 'none') {
      area.style.display = 'none';
      return;
    }
    area.style.display = '';
    content.textContent = '加载中...';
    try {
      const data = await api('/api/container/logs?tail=100');
      content.textContent = data.logs || '(无日志)';
    } catch (e) {
      content.textContent = '加载失败：' + e.message;
    }
  });
}

async function renderAdminContainers(el) {
  let containers;
  try { containers = await api('/api/usage/admin/containers'); } catch (_) { containers = null; }
  const list = containers ? (containers.containers || []) : [];
  const server = containers ? (containers.server || null) : null;
  const dockerContainers = containers ? (containers.containers_raw || []) : [];

  // 管理员的容器操作仅对自己容器有效（后端限制），其他容器只读状态
  const adminUserId = getUser().user_id;

  el.innerHTML = `
    <h2>容器管理</h2>

    ${server ? `
    <div class="card" style="margin-bottom:20px">
      <h3>服务器概览</h3>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
        <div><strong>磁盘</strong><br>${server.disk.used} / 总共 ${server.disk.total} (${server.disk.pct})</div>
        <div><strong>内存</strong><br>${server.memory.used} / ${server.memory.total}</div>
        <div><strong>负载</strong><br>${server.load}</div>
        <div><strong>容器</strong><br>${dockerContainers.length} 个</div>
      </div>
      ${dockerContainers.length > 0 ? `
        <table style="margin-top:12px"><thead><tr><th>名称</th><th>状态</th><th>端口</th><th>占用</th><th>内存</th></tr></thead><tbody>
        ${dockerContainers.map(dc => `
          <tr>
            <td>${dc.name}</td>
            <td><span class="badge ${dc.status==='running'?'badge-ok':dc.status==='exited'?'badge-err':'badge-neutral'}">${dc.status}</span></td>
            <td>${dc.ports || '-'}</td>
            <td>${dc.size || '-'}</td>
            <td>${dc.memory || '-'}</td>
          </tr>
        `).join('')}
        </tbody></table>
      ` : ''}
    </div>
    ` : ''}

    <div class="filter-bar" style="margin-bottom:12px">
      <button class="btn btn-sm" id="btn-containers-refresh">刷新</button>
      <span style="color:var(--text2);font-size:.85rem">共 ${list.length} 个容器</span>
      <span style="flex:1"></span>
      <button class="btn btn-sm" id="btn-my-logs" style="margin-left:auto">我的容器日志</button>
    </div>

    ${list.length === 0 ? `<div class="card"><div class="empty">暂无已审批的用户容器</div></div>` : `
    <div class="table-wrap">
      <table>
        <thead><tr><th>用户</th><th>容器</th><th>状态</th><th>微信</th><th style="width:auto">操作</th></tr></thead>
        <tbody>
          ${list.map(c => `
            <tr>
              <td>${escapeHtml(c.email || '-')}</td>
              <td><span class="badge badge-neutral">hermes-${c.container_id || c.user_id || '-'}</span></td>
              <td><span class="badge ${c.status==='running'?'badge-ok':c.status==='stopped'?'badge-warn':'badge-neutral'}">${c.status || '-'}</span></td>
              <td>${c.wechat_bound ? '<span class="badge badge-ok">已绑定</span>' : '<span style="color:var(--text3)">—</span>'}</td>
              <td>
                ${c.user_id === adminUserId ? `
                <div class="btn-group">
                  <button class="btn btn-sm btn-primary btn-my-start" ${c.status==='running'?'disabled':''}>启动</button>
                  <button class="btn btn-sm btn-my-stop" ${c.status!=='running'?'disabled':''}>停止</button>
                  <button class="btn btn-sm btn-my-restart">重启</button>
                </div>` : '<span style="color:var(--text3);font-size:.78rem">仅可管理自己的容器</span>'}
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>`}

    <div id="my-logs-area" style="display:none;margin-top:16px">
      <div class="card">
        <h3>我的容器日志</h3>
        <div class="log-view" id="my-logs-content">加载中...</div>
      </div>
    </div>
  `;

  document.getElementById('btn-containers-refresh')?.addEventListener('click', () => navigate('container'));

  // 日志
  document.getElementById('btn-my-logs')?.addEventListener('click', async () => {
    const area = document.getElementById('my-logs-area');
    const content = document.getElementById('my-logs-content');
    if (area.style.display !== 'none') { area.style.display = 'none'; return; }
    area.style.display = '';
    content.textContent = '加载中...';
    try {
      const data = await api('/api/container/logs?tail=100');
      content.textContent = data.logs || '(无日志)';
    } catch (e) { content.textContent = '加载失败：' + e.message; }
  });

  // 容器操作（管理员自己的容器）
  async function myAction(act) {
    const labels = { start: '启动', stop: '停止', restart: '重启' };
    try {
      await api(`/api/container/${act}`, { method: 'POST' });
      toast(`容器${labels[act]}成功`, 'ok');
      navigate('container');
    } catch (e) { toast(e.message, 'err'); }
  }

  document.querySelector('.btn-my-start')?.addEventListener('click', () => myAction('start'));
  document.querySelector('.btn-my-stop')?.addEventListener('click', () => myAction('stop'));
  document.querySelector('.btn-my-restart')?.addEventListener('click', () => myAction('restart'));
}
