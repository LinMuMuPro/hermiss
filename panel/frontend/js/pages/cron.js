/* pages/cron.js - proactive reply and cron job overview */

window.Pages.cron = async function(el) {
  const escapeHtml = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  const fmt = (value) => (value === 0 || value) ? escapeHtml(value) : '-';
  const renderActive = (active) => {
    if (!active) return '<div class="empty compact">\u6682\u65e0\u4e3b\u52a8\u56de\u8bbf\u94fe</div>';
    const state = active.short_term_user_state;
    const base = active.state_base;
    const line = (key, label, value) => {
      const display = fmt(value);
      return `<div class="cron-checkin-line"><strong>${escapeHtml(key)}\uff08${escapeHtml(label)}\uff09</strong><em>${display}</em></div>`;
    };
    const status = active.cancelled ? '\u5df2\u53d6\u6d88' : '\u8fdb\u884c\u4e2d';
    const rows = [
      line('status', '\u72b6\u6001', status),
      line('trigger_local_time', '\u89e6\u53d1\u65f6\u95f4', active.trigger_local_time),
      line('delay', '\u5ef6\u8fdf', active.effective_delay || (active.check_in_minutes ? active.check_in_minutes + 'm' : '-')),
      line('followup_stage', '\u56de\u8bbf\u9636\u6bb5', `${fmt(active.followup_stage)} / ${fmt(active.max_followup_stage)}`),
      line('last_activity_hint', '\u6700\u8fd1\u6d3b\u52a8', active.last_activity_hint),
      line('style_hint', '\u98ce\u683c\u63d0\u793a', active.style_hint),
      line('short_state', '\u77ed\u671f\u72b6\u6001', state ? (state.text || JSON.stringify(state)) : '-'),
      line('state_base', '\u72b6\u6001\u5e95\u5ea7', base ? (base.summary || JSON.stringify(base)) : '-'),
    ];
    return `<div class="cron-checkin-card">${rows.join('')}</div>`;
  };

  const renderJobs = (jobs) => {
    if (!jobs.length) return '<div class="empty compact">暂无定时任务</div>';
    return `
      <div class="table-wrap cron-table-wrap">
        <table>
          <thead><tr><th>ID</th><th>类型</th><th>状态</th><th>下次运行</th><th>投递</th><th>操作</th></tr></thead>
          <tbody>
            ${jobs.map(job => `
              <tr>
                <td class="mono">${fmt(job.id)}</td>
                <td>${fmt(job.type || job.name)}</td>
                <td><span class="badge ${job.enabled ? 'badge-ok' : 'badge-neutral'}">${fmt(job.state)}</span></td>
                <td>${fmt(job.next_run_at || job.schedule)}</td>
                <td class="mono">${fmt(job.deliver)}</td>
                <td><button class="btn btn-sm btn-danger btn-cancel-cron" data-id="${escapeHtml(job.id)}">取消</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  };

  let data;
  try { data = await api('/api/cron/status'); } catch (_) { data = null; }
  const active = data?.active_checkin;
  const jobs = data?.jobs || [];

  el.innerHTML = `
    <div class="page-head cron-page-head">
      <div class="cron-title-row">
        <h2>\u5b9a\u65f6\u4efb\u52a1</h2>
        <p class="page-subtitle">\u67e5\u770b\u4e3b\u52a8\u56de\u8bbf\u94fe\u3001\u7528\u6237\u72b6\u6001\u4e0a\u4e0b\u6587\u548c Hermes \u5b9a\u65f6\u4efb\u52a1\u3002</p>
      </div>
      <button class="btn btn-sm" id="btn-cron-refresh">\u5237\u65b0</button>
    </div>
    <div class="settings-single-column">
      <div class="card">
        <div class="card-head">
          <h3>主动回访</h3>
          ${active && !active.cancelled ? '<button class="btn btn-sm btn-danger" id="btn-cancel-active">取消当前回访链</button>' : ''}
        </div>
        ${renderActive(active)}
      </div>
      <div class="card">
        <div class="card-head">
          <h3>任务列表</h3>
          <span class="form-hint">共 ${jobs.length} 个任务</span>
        </div>
        ${renderJobs(jobs)}
      </div>
    </div>
  `;

  document.getElementById('btn-cron-refresh')?.addEventListener('click', () => navigate('cron'));
  document.getElementById('btn-cancel-active')?.addEventListener('click', async () => {
    const ok = await dialogConfirm('确定取消当前主动回访链？');
    if (!ok) return;
    try {
      await api('/api/cron/cancel-active', { method: 'POST' });
      toast('已取消主动回访链', 'ok');
      navigate('cron');
    } catch (e) { toast(e.message, 'err'); }
  });
  document.querySelectorAll('.btn-cancel-cron').forEach(btn => {
    btn.addEventListener('click', async function() {
      const ok = await dialogConfirm(`确定取消任务 ${this.dataset.id}？`);
      if (!ok) return;
      try {
        await api(`/api/cron/cancel/${encodeURIComponent(this.dataset.id)}`, { method: 'POST' });
        toast('任务已取消', 'ok');
        navigate('cron');
      } catch (e) { toast(e.message, 'err'); }
    });
  });
};
