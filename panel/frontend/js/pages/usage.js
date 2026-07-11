/* ── pages/usage.js ──
   v5.0 — replaces dashboard. User sees token usage, admin sees global summary + containers */

let usageDays = 7;

window.Pages.usage = async function(el) {
  const admin = isAdmin();

  if (admin) {
    await renderAdminUsage(el);
  } else {
    await renderUserUsage(el);
  }
};

async function renderUserUsage(el) {
  let data;
  try { data = await api(`/api/usage/my?days=${usageDays}`); } catch (_) { data = null; }

  const total = data ? (data.total_tokens || 0) : 0;
  const daily = data ? (data.daily || []) : [];
  const hitRate = data ? (data.cache_hit_rate || 0) : 0;

  el.innerHTML = `
    <h2>用量统计</h2>

    <div class="card">
      <h3>Token 消耗（${usageDays} 天）</h3>
      <div style="font-size:2rem;font-weight:600;color:var(--accent);margin:12px 0">
        ${total.toLocaleString()} <span style="font-size:.85rem;font-weight:400;color:var(--text2)">tokens</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:8px 0">
        <div style="text-align:center"><div style="font-size:1.1rem;font-weight:600">${(data?.input_tokens || 0).toLocaleString()}</div><div style="font-size:.75rem;color:var(--text2)">Input</div></div>
        <div style="text-align:center"><div style="font-size:1.1rem;font-weight:600">${(data?.output_tokens || 0).toLocaleString()}</div><div style="font-size:.75rem;color:var(--text2)">Output</div></div>
        <div style="text-align:center"><div style="font-size:1.1rem;font-weight:600;color:${hitRate > 50 ? 'var(--accent)' : 'var(--text2)'}">${hitRate}%</div><div style="font-size:.75rem;color:var(--text2)">缓存命中</div></div>
      </div>
      <div class="filter-bar" style="margin-bottom:12px">
        ${[7,14,30].map(d => `
          <button class="btn btn-sm ${usageDays===d?'btn-primary':''}" id="btn-days-${d}">${d} 天</button>
        `).join('')}
        <button class="btn btn-sm" id="btn-usage-refresh">刷新</button>
      </div>
    </div>

    <div class="card">
      <h3>每日趋势</h3>
      ${daily.length === 0 ? '<div class="empty">暂无数据</div>' : `
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>Tokens</th></tr></thead>
          <tbody>
            ${daily.map(d => `
              <tr>
                <td>${d.date || '-'}</td>
                <td>${(d.tokens || 0).toLocaleString()}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`}
    </div>
  `;

  // Days buttons
  [7,14,30].forEach(d => {
    document.getElementById(`btn-days-${d}`)?.addEventListener('click', () => {
      usageDays = d;
      navigate('usage');
    });
  });
  document.getElementById('btn-usage-refresh')?.addEventListener('click', () => navigate('usage'));
}

async function renderAdminUsage(el) {
  let containers, global;
  try { containers = await api('/api/usage/admin/containers'); } catch (_) { containers = null; }
  try { global = await api(`/api/usage/admin/global?days=${usageDays}`); } catch (_) { global = null; }

  const containerList = containers ? (containers.containers || []) : [];
  const totalTokens = global ? (global.total_tokens || 0) : 0;
  const userCount = global ? (global.user_count || 0) : 0;
  const users = global ? (global.users || []) : [];
  const daily = global ? (global.daily || []) : [];
  const globalCacheRate = users.length > 0
    ? Math.round(users.reduce((s, u) => s + (u.cache_read_tokens || 0), 0) / (users.reduce((s, u) => s + (u.input_tokens || 0) + (u.cache_read_tokens || 0), 0) || 1) * 100)
    : 0;

  el.innerHTML = `
    <h2>用量统计</h2>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px">
      <div class="card" style="text-align:center">
        <div style="font-size:1.5rem;font-weight:600;color:var(--accent)">${totalTokens.toLocaleString()}</div>
        <div style="font-size:.8rem;color:var(--text2);margin-top:4px">总 Token 消耗</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:1.5rem;font-weight:600;color:var(--accent)">${userCount}</div>
        <div style="font-size:.8rem;color:var(--text2);margin-top:4px">活跃用户</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:1.5rem;font-weight:600;color:var(--accent)">${containerList.length}</div>
        <div style="font-size:.8rem;color:var(--text2);margin-top:4px">容器数</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:1.5rem;font-weight:600;color:${globalCacheRate > 50 ? 'var(--accent)' : 'var(--text2)'}">${globalCacheRate}%</div>
        <div style="font-size:.8rem;color:var(--text2);margin-top:4px">缓存命中率</div>
      </div>
    </div>

    <div class="filter-bar" style="margin-bottom:12px">
      ${[7,14,30].map(d => `
        <button class="btn btn-sm ${usageDays===d?'btn-primary':''}" id="btn-days-${d}">${d} 天</button>
      `).join('')}
      <button class="btn btn-sm" id="btn-usage-refresh">刷新</button>
    </div>

    <div class="card">
      <h3>容器列表</h3>
      ${containerList.length === 0 ? '<div class="empty">暂无容器</div>' : `
      <div class="table-wrap">
        <table>
          <thead><tr><th>用户</th><th>容器</th><th>状态</th><th>面板</th></tr></thead>
          <tbody>
            ${containerList.map(c => `
              <tr>
                <td>${escapeHtml(c.email || c.user_id || '-')}</td>
                <td><span class="badge badge-neutral">${c.container_name || c.container_id || '-'}</span></td>
                <td><span class="badge ${c.status==='running'?'badge-ok':c.status==='stopped'?'badge-warn':'badge-neutral'}">${c.status || '-'}</span></td>
                <td>${c.panel_url ? `<a href="${c.panel_url}" target="_blank">${c.panel_url}</a>` : '<span style="color:var(--text3)">—</span>'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`}
    </div>

    <div class="card">
      <h3>每日趋势（全局）</h3>
      ${daily.length === 0 ? '<div class="empty">暂无数据</div>' : `
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>Tokens</th></tr></thead>
          <tbody>
            ${daily.map(d => `
              <tr>
                <td>${d.date || '-'}</td>
                <td>${(d.tokens || 0).toLocaleString()}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`}
    </div>
  `;

  // Days buttons
  [7,14,30].forEach(d => {
    document.getElementById(`btn-days-${d}`)?.addEventListener('click', () => {
      usageDays = d;
      navigate('usage');
    });
  });
  document.getElementById('btn-usage-refresh')?.addEventListener('click', () => navigate('usage'));
}
