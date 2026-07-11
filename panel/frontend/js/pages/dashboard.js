/* ── pages/dashboard.js ──
   看板：融合容器 + 监控 + 用量。
   管理员：KPI 卡片 + 主机资源 + 容器管理 + 用量趋势
   普通用户：Token 概览 + 容器状态 + 每日趋势 */

let dashDays = 7;

window.Pages.dashboard = async function(el) {
  const admin = isAdmin();
  dashDays = dashDays || 7;

  if (admin) {
    await renderAdminDashboard(el);
  } else {
    await renderUserDashboard(el);
  }
};

/* ══════════════════════════════════════════════
   普通用户看板
   ══════════════════════════════════════════════ */
async function renderUserDashboard(el) {
  let usage, cStatus;
  try { usage = await api(`/api/usage/my?days=${dashDays}`); } catch (_) { usage = null; }
  try { cStatus = await api('/api/container/status'); } catch (_) { cStatus = null; }

  const total = usage ? (usage.total_tokens || 0) : 0;
  const daily = usage ? (usage.daily || []) : [];
  const hitRate = usage ? (usage.cache_hit_rate || 0) : 0;
  const hasContainer = cStatus && cStatus.status !== 'not_created';
  const running = cStatus && cStatus.status === 'running';

  el.innerHTML = `
    <h2>看板</h2>

    <!-- Token 概览 -->
    <div class="card">
      <h3>Token 消耗（${dashDays} 天）</h3>
      <div class="kpi-big">${total.toLocaleString()} <span class="kpi-unit">tokens</span></div>
      <div class="kpi-row">
        <div class="kpi-item">
          <div class="kpi-val">${(usage?.input_tokens || 0).toLocaleString()}</div>
          <div class="kpi-label">Input</div>
        </div>
        <div class="kpi-item">
          <div class="kpi-val">${(usage?.output_tokens || 0).toLocaleString()}</div>
          <div class="kpi-label">Output</div>
        </div>
        <div class="kpi-item">
          <div class="kpi-val" style="color:${hitRate > 50 ? 'var(--accent)' : 'var(--text2)'}">${hitRate}%</div>
          <div class="kpi-label">缓存命中</div>
        </div>
      </div>
      <div class="filter-bar">
        ${dayButtons()}
        <button class="btn btn-sm" id="btn-dash-refresh">刷新</button>
      </div>
    </div>

    <!-- 容器状态 -->
    <div class="card">
      <h3>容器状态</h3>
      <div class="inline-status">
        <span class="dot ${running ? 'dot-ok' : cStatus && cStatus.status === 'stopped' ? 'dot-warn' : 'dot-err'}"></span>
        <span>${cStatus ? cStatus.status : '未知'}</span>
        ${cStatus && cStatus.name ? `<span class="badge badge-neutral" style="margin-left:8px">${escapeHtml(cStatus.name)}</span>` : ''}
        ${cStatus && cStatus.panel_url ? `<a href="${escapeHtml(cStatus.panel_url)}" target="_blank" class="panel-link">${escapeHtml(cStatus.panel_url)}</a>` : ''}
      </div>
      ${!hasContainer ? `
        <div class="empty" style="padding:16px">容器正在自动创建，请稍后刷新</div>
      ` : `
        <div class="btn-group">
          <button class="btn btn-sm" id="btn-c-refresh">刷新</button>
          <button class="btn btn-sm" id="btn-c-logs">查看日志</button>
        </div>
        <div id="dash-logs-area" style="display:none;margin-top:12px">
          <div class="log-view" id="dash-logs-content">加载中...</div>
        </div>
      `}
    </div>

    <!-- 每日趋势 -->
    <div class="card">
      <h3>每日趋势</h3>
      ${daily.length === 0 ? '<div class="empty" style="padding:24px">暂无数据</div>' : `
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>Tokens</th></tr></thead>
          <tbody>
            ${daily.map(d => `<tr><td>${d.date || '-'}</td><td>${(d.tokens || 0).toLocaleString()}</td></tr>`).join('')}
          </tbody>
        </table>
      </div>`}
    </div>
  `;

  // Events
  [7,14,30].forEach(d => {
    document.getElementById(`btn-days-${d}`)?.addEventListener('click', () => { dashDays = d; navigate('dashboard'); });
  });
  document.getElementById('btn-dash-refresh')?.addEventListener('click', () => navigate('dashboard'));
  document.getElementById('btn-c-refresh')?.addEventListener('click', () => navigate('dashboard'));

  document.getElementById('btn-c-logs')?.addEventListener('click', async () => {
    const area = document.getElementById('dash-logs-area');
    const content = document.getElementById('dash-logs-content');
    if (area.style.display !== 'none') { area.style.display = 'none'; return; }
    area.style.display = '';
    content.textContent = '加载中...';
    try {
      const data = await api('/api/container/logs?tail=100');
      content.textContent = data.logs || '(无日志)';
    } catch (e) { content.textContent = '加载失败：' + e.message; }
  });
}

/* ══════════════════════════════════════════════
   管理员看板
   ══════════════════════════════════════════════ */
async function renderAdminDashboard(el) {
  // 并行请求（server 字段已含在 containers 响应中，无需单独调 server-stats）
  let usage, containers, myStatus;
  try { usage = await api(`/api/usage/admin/global?days=${dashDays}`); } catch (_) { usage = null; }
  try { containers = await api('/api/usage/admin/containers'); } catch (_) { containers = null; }
  try { myStatus = await api('/api/container/status'); } catch (_) { myStatus = null; }

  const containerList = containers ? (containers.containers || []) : [];
  const server = containers ? containers.server : null;
  const containersRaw = containers ? (containers.containers_raw || []) : [];
  const totalTokens = usage ? (usage.total_tokens || 0) : 0;
  const userCount = usage ? (usage.user_count || 0) : 0;
  const daily = usage ? (usage.daily || []) : [];
  const users = usage ? (usage.users || []) : [];

  const globalCacheRate = users.length > 0
    ? Math.round(users.reduce((s, u) => s + (u.cache_read_tokens || 0), 0) /
        (users.reduce((s, u) => s + (u.input_tokens || 0) + (u.cache_read_tokens || 0), 0) || 1) * 100)
    : 0;

  // 服务器资源解析
  const s = server;
  const loadArr = s ? parseLoad(s.load) : ['-', '-', '-'];

  // 容器运行/停止统计（来自管理员容器列表）
  const runningCnt = containerList.filter(c => c.status === 'running').length;
  const stoppedCnt = containerList.filter(c => c.status !== 'running' && c.status !== 'none').length;

  const diskPct = s ? parsePct(s.disk?.pct) : 0;
  const diskUsed = s ? (s.disk?.used || '-') : '-';
  const diskTotal = s ? (s.disk?.total || (parseGi(s.disk?.used) + parseGi(s.disk?.avail)).toFixed(1) + 'G') : '-';

  const memTotal = s ? parseGi(s.memory?.total) : 0;
  const memUsed = s ? parseGi(s.memory?.used) : 0;
  const memPct = memTotal > 0 ? Math.round(memUsed / memTotal * 100) : 0;

  const adminUserId = getUser().user_id;

  el.innerHTML = `
    <h2>看板</h2>

    <!-- KPI 卡片行 -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-val-large">${userCount}</div>
        <div class="kpi-sub">用户</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val-large">
          <span style="color:var(--accent)">${runningCnt}</span>
          <span style="color:var(--text3);font-weight:300;margin:0 4px">/</span>
          <span style="color:var(--text2)">${stoppedCnt}</span>
        </div>
        <div class="kpi-sub">容器（运行/停止）</div>
      </div>
    </div>

    <!-- 服务器概览 -->
    ${s ? `
    <div class="card" style="margin-bottom:20px">
      <div class="card-head">
        <h3>服务器概览</h3>
        <button class="btn btn-sm" id="btn-dash-refresh">刷新</button>
      </div>
      <div class="server-overview">
        <div class="so-item">
          <span class="so-label">磁盘</span>
          <span class="so-val" style="color:${barColor(diskPct)}">${diskUsed} / ${diskTotal}（${diskPct}%）</span>
        </div>
        <div class="so-item">
          <span class="so-label">内存</span>
          <span class="so-val" style="color:${barColor(memPct)}">${memUsed.toFixed(1)}G / ${memTotal.toFixed(1)}G（${memPct}%）</span>
        </div>
        <div class="so-item">
          <span class="so-label">负载</span>
          <span class="so-val" style="color:${parseFloat(loadArr[0]) > 2 ? 'var(--warning)' : 'var(--accent)'}">${loadArr[0]} / ${loadArr[1]} / ${loadArr[2]}</span>
        </div>
        <div class="so-item">
          <span class="so-label">容器</span>
          <span class="so-val">${containersRaw.length} 个</span>
        </div>
      </div>
      ${resGauge('磁盘', diskUsed, diskTotal, diskPct)}
      ${resGauge('内存', memUsed.toFixed(1) + 'G', memTotal.toFixed(1) + 'G', memPct)}
    </div>
    ` : `
    <div class="card" style="margin-bottom:20px">
      <div class="card-head">
        <h3>服务器概览</h3>
        <button class="btn btn-sm" id="btn-dash-refresh">刷新</button>
      </div>
      <div class="empty" style="padding:24px">服务器数据加载失败</div>
    </div>`}

    <!-- 容器管理 -->
    <div class="card">
      <div class="card-head">
        <h3>容器管理（${containerList.length}）</h3>
        <div class="btn-group">
          <button class="btn btn-sm" id="btn-ct-refresh">刷新</button>
          <button class="btn btn-sm" id="btn-my-logs">我的容器日志</button>
        </div>
      </div>
      ${containerList.length === 0 ? '<div class="empty" style="padding:24px">暂无已审批的用户容器</div>' : `
      <div class="table-wrap">
        <table>
          <thead><tr><th>用户</th><th>容器</th><th>状态</th><th>占用</th><th>微信</th><th>操作</th></tr></thead>
          <tbody>
            ${containerList.map(c => `
              <tr>
                <td>${escapeHtml(c.email || '-')}</td>
                <td><span class="badge badge-neutral">hermes-${c.container_id || c.user_id || '-'}</span></td>
                <td><span class="badge ${c.status==='running'?'badge-ok':c.status==='stopped'?'badge-warn':'badge-neutral'}">${c.status || '-'}</span></td>
                <td style="font-size:.78rem;font-family:monospace;color:var(--text)">
                  ${containerUsage(containersRaw.find(dc => dc.name === c.container_id) || {})}
                </td>
                <td>${c.wechat_bound ? '<span class="badge badge-ok">已绑定</span>' : '<span style="color:var(--text3)">—</span>'}</td>
                <td>
                  ${c.user_id === adminUserId ? `
                  <div class="btn-group">
                    <button class="btn btn-sm btn-primary btn-my-start" data-cid="${c.container_id || c.user_id}" ${c.status==='running'?'disabled':''}>启动</button>
                    <button class="btn btn-sm btn-my-stop" data-cid="${c.container_id || c.user_id}" ${c.status!=='running'?'disabled':''}>停止</button>
                    <button class="btn btn-sm btn-my-restart" data-cid="${c.container_id || c.user_id}">重启</button>
                  </div>` : '<span style="color:var(--text3);font-size:.78rem">仅可管理自己的容器</span>'}
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`}

      <div id="dash-my-logs" style="display:none;margin-top:12px">
        <div class="log-view" id="dash-my-logs-content">加载中...</div>
      </div>
    </div>

  `;

  // ── Events ──

  // Day buttons
  [7,14,30].forEach(d => {
    document.getElementById(`btn-days-${d}`)?.addEventListener('click', () => { dashDays = d; navigate('dashboard'); });
  });

  // Refresh
  document.getElementById('btn-dash-refresh')?.addEventListener('click', () => navigate('dashboard'));
  document.getElementById('btn-ct-refresh')?.addEventListener('click', () => navigate('dashboard'));

  // My logs toggle
  document.getElementById('btn-my-logs')?.addEventListener('click', async () => {
    const area = document.getElementById('dash-my-logs');
    const content = document.getElementById('dash-my-logs-content');
    if (area.style.display !== 'none') { area.style.display = 'none'; return; }
    area.style.display = '';
    content.textContent = '加载中...';
    try {
      const data = await api('/api/container/logs?tail=100');
      content.textContent = data.logs || '(无日志)';
    } catch (e) { content.textContent = '加载失败：' + e.message; }
  });

  // Container operations (admin's own container)
  async function myAction(btn, act) {
    const labels = { start: '启动', stop: '停止', restart: '重启' };
    btn.disabled = true;
    btn.textContent = `${labels[act]}中...`;
    try {
      await api(`/api/container/${act}`, { method: 'POST' });
      toast(`容器${labels[act]}成功`, 'ok');
      navigate('dashboard');
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = labels[act];
    }
  }

  el.querySelectorAll('.btn-my-start').forEach(b => b.addEventListener('click', () => myAction(b, 'start')));
  el.querySelectorAll('.btn-my-stop').forEach(b => b.addEventListener('click', () => myAction(b, 'stop')));
  el.querySelectorAll('.btn-my-restart').forEach(b => b.addEventListener('click', () => myAction(b, 'restart')));
}

/* ══════════════════════════════════════════════
   Helpers
   ══════════════════════════════════════════════ */

function dayButtons() {
  return [7,14,30].map(d =>
    `<button class="btn btn-sm ${dashDays===d?'btn-primary':''}" id="btn-days-${d}">${d} 天</button>`
  ).join('');
}

function parseGi(val) {
  if (!val) return 0;
  val = String(val).trim();
  // Handle numeric values (already in GB) or values with unit suffixes
  const num = parseFloat(val);
  if (isNaN(num)) return 0;
  // Convert to GB based on suffix
  const m = val.match(/(\d+\.?\d*)\s*(Ti|Gi|Mi|Ki|T|G|M|K|B)?/i);
  if (!m) return num;
  const v = parseFloat(m[1]);
  const unit = (m[2] || '').toUpperCase();
  if (unit.startsWith('T')) return v * 1024;
  if (unit.startsWith('M')) return v / 1024;
  if (unit.startsWith('K')) return v / (1024 * 1024);
  return v; // G, B, or no suffix
}

function parsePct(val) {
  if (!val) return 0;
  return parseInt(String(val).replace('%', '')) || 0;
}

function parseLoad(str) {
  if (!str) return ['-', '-', '-'];
  return str.split(',').map(v => v.trim());
}

function containerUsage(raw) {
  if (!raw || (!raw.cpu && !raw.memory && !raw.net_io && !raw.size)) return '-';
  return `
    <div>DISK ${escapeHtml(raw.size || '-')}</div>
    <div>CPU ${escapeHtml(raw.cpu || '-')}</div>
    <div>MEM ${escapeHtml(raw.memory || '-')} (${escapeHtml(raw.memory_percent || '-')})</div>
    <div>NET ${escapeHtml(raw.net_io || '-')}</div>
  `;
}

function barColor(pct) {
  if (pct >= 90) return 'var(--danger)';
  if (pct >= 70) return 'var(--warning)';
  return 'var(--accent)';
}

function barBg(pct) {
  if (pct >= 90) return 'var(--danger-muted)';
  if (pct >= 70) return '#fff3e0';
  return 'var(--accent-muted)';
}

function fmtSize(val) {
  if (val == null || val === '') return '—';
  // Already a string like "123MB" or "1.2G" — pass through
  if (typeof val === 'string' && /[A-Za-z]/.test(val)) return val;
  // Numeric value in bytes — convert to MB
  const n = parseFloat(val);
  if (isNaN(n)) return String(val);
  if (n >= 1e9) return (n / 1e9).toFixed(1) + ' GB';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + ' KB';
  return n + ' B';
}

function resGauge(label, used, total, pct) {
  const color = barColor(pct);
  const bg = barBg(pct);
  return `
    <div class="res-gauge">
      <div class="res-gauge-head">
        <span class="res-gauge-label">${label}</span>
        <span class="res-gauge-val" style="color:${color}">${used} / ${total}（${pct}%）</span>
      </div>
      <div class="res-bar-track" style="background:${bg}">
        <div class="res-bar-fill" style="width:${pct}%;background:${color}"></div>
      </div>
    </div>`;
}
