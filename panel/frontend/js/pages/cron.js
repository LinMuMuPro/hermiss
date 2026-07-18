/* pages/cron.js - proactive reply and cron job overview */

const cronEscapeHtml = value => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

function cronFormatTime(value) {
  if (!value) return '未设置';
  return cronEscapeHtml(String(value).replace('T', ' ').replace(/\.\d+/, ''));
}

function cronStatusBadge(job) {
  if (!job.enabled || job.state === 'paused') return '<span class="badge badge-neutral">停用</span>';
  if (job.state === 'scheduled' || job.state === 'active') return '<span class="badge badge-ok">等待触发</span>';
  if (job.last_error) return '<span class="badge badge-err">异常</span>';
  return `<span class="badge badge-neutral">${cronEscapeHtml(job.state || '未知')}</span>`;
}

function renderActiveCheckin(active) {
  if (!active || active.cancelled) {
    return `
      <div class="card cron-active-card">
        <div class="card-head">
          <div>
            <h3>主动回访</h3>
            <p class="form-hint">当前没有生效中的主动回访链。</p>
          </div>
          <span class="badge badge-neutral">无任务</span>
        </div>
      </div>
    `;
  }

  const base = active.state_base || {};
  const shortState = active.short_term_user_state || {};
  const jobCount = Array.isArray(active.job_ids) ? active.job_ids.length : 0;

  return `
    <div class="card cron-active-card">
      <div class="card-head">
        <div>
          <h3>主动回访</h3>
          <p class="form-hint">由状态底座和最近上下文创建；用户回复后会自动取消后续回访。</p>
        </div>
        <div class="btn-group">
          <span class="badge badge-ok">生效中</span>
          <button class="btn btn-sm btn-danger" id="btn-cancel-active-cron">取消回访</button>
        </div>
      </div>

      <div class="cron-summary-grid">
        <div class="cron-summary-item">
          <span>首次触发</span>
          <strong>${cronFormatTime(active.trigger_local_time || active.fire_at)}</strong>
        </div>
        <div class="cron-summary-item">
          <span>延迟</span>
          <strong>${cronEscapeHtml(active.effective_delay || `${active.check_in_minutes || 0}m`)}</strong>
        </div>
        <div class="cron-summary-item">
          <span>回访链</span>
          <strong>${jobCount || 1} 个任务</strong>
        </div>
        <div class="cron-summary-item">
          <span>最后用户消息</span>
          <strong>${cronFormatTime(active.last_user_message_at)}</strong>
        </div>
      </div>

      <div class="cron-context-grid">
        <div>
          <h4>状态底座</h4>
          <p>${cronEscapeHtml(base.summary || '暂无摘要')}</p>
          ${base.current_state ? `<p class="form-hint">当前状态：${cronEscapeHtml(base.current_state)}</p>` : ''}
          ${base.caution ? `<p class="form-hint">回复注意：${cronEscapeHtml(base.caution)}</p>` : ''}
        </div>
        <div>
          <h4>短期状态</h4>
          <p>${cronEscapeHtml(shortState.text || '暂无短期状态')}</p>
          ${active.last_activity_hint ? `<p class="form-hint">最后活动：${cronEscapeHtml(active.last_activity_hint)}</p>` : ''}
          ${active.style_hint ? `<p class="form-hint">策略：${cronEscapeHtml(active.style_hint)}</p>` : ''}
        </div>
      </div>
    </div>
  `;
}

function renderJobsTable(jobs) {
  if (!jobs.length) return '<div class="empty">暂无定时任务</div>';
  return `
    <div class="table-wrap cron-table-wrap">
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>状态</th>
            <th>下次触发</th>
            <th>计划</th>
            <th>发送目标</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${jobs.map(job => `
            <tr>
              <td>
                <strong>${cronEscapeHtml(job.type || '定时任务')}</strong>
                <div class="form-hint mono">${cronEscapeHtml(job.id || '-')}</div>
              </td>
              <td>${cronStatusBadge(job)}</td>
              <td>${cronFormatTime(job.next_run_at)}</td>
              <td>${cronEscapeHtml(job.schedule || '-')}</td>
              <td class="mono">${cronEscapeHtml(job.deliver || '-')}</td>
              <td>
                <button class="btn btn-sm btn-danger cron-cancel-job" data-id="${cronEscapeHtml(job.id)}">取消</button>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

document.addEventListener('click', async e => {
  const refreshBtn = e.target.closest('#btn-refresh-cron');
  const cancelActiveBtn = e.target.closest('#btn-cancel-active-cron');
  const cancelJobBtn = e.target.closest('.cron-cancel-job');

  if (refreshBtn) navigate('cron');

  if (cancelActiveBtn) {
    const ok = await dialogConfirm('确定取消当前主动回访链？');
    if (!ok) return;
    try {
      await api('/api/cron/cancel-active', { method: 'POST' });
      toast('已取消主动回访', 'ok');
      navigate('cron');
    } catch (err) {
      toast(err.message, 'err');
    }
  }

  if (cancelJobBtn) {
    const id = cancelJobBtn.dataset.id;
    const ok = await dialogConfirm(`确定取消任务 ${id}？`);
    if (!ok) return;
    try {
      await api(`/api/cron/cancel/${encodeURIComponent(id)}`, { method: 'POST' });
      toast('已取消任务', 'ok');
      navigate('cron');
    } catch (err) {
      toast(err.message, 'err');
    }
  }
});

window.Pages.cron = async function(el) {
  let data;
  try {
    data = await api('/api/cron/status');
  } catch (err) {
    el.innerHTML = `<div class="empty">读取定时任务失败：${cronEscapeHtml(err.message)}</div>`;
    return;
  }

  const jobs = data.jobs || [];
  el.innerHTML = `
    <div class="page-header">
      <div>
        <h1>定时任务</h1>
        <p class="form-hint">查看主动回访链、Hermiss Cron 任务和下次触发时间。</p>
      </div>
      <button class="btn btn-sm" id="btn-refresh-cron">刷新</button>
    </div>

    ${renderActiveCheckin(data.active_checkin)}

    <div class="card">
      <div class="card-head">
        <div>
          <h3>任务列表</h3>
          <p class="form-hint">包含主动回访预创建的多阶段任务，以及其他 Hermes 定时任务。</p>
        </div>
        <span class="badge badge-neutral">${jobs.length} 个任务</span>
      </div>
      ${renderJobsTable(jobs)}
    </div>
  `;
};
