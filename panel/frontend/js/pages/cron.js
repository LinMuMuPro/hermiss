/* ── pages/cron.js ── */

window.Pages.cron = async function(el) {
  let data;
  try { data = await api('/api/cron/list'); } catch (_) { data = null; }

  el.innerHTML = `
    <h2>定时任务</h2>

    <div class="btn-group" style="margin-bottom:12px">
      <button class="btn btn-sm" id="btn-cron-refresh">刷新</button>
    </div>

    <div class="log-view" id="cron-output">${data ? data.output || '(无定时任务)' : '加载失败'}</div>

    ${isAdmin() ? `
    <div class="btn-group" style="margin-top:12px">
      <button class="btn btn-sm btn-danger" id="btn-cron-cancel">取消任务</button>
    </div>` : ''}
  `;

  document.getElementById('btn-cron-refresh')?.addEventListener('click', () => navigate('cron'));

  document.getElementById('btn-cron-cancel')?.addEventListener('click', async () => {
    const jobId = await dialogPrompt('输入要取消的 Job ID');
    if (!jobId) return;
    try {
      const data = await api(`/api/cron/cancel/${jobId}`, { method: 'POST' });
      toast(data.output || '已取消', 'ok');
      navigate('cron');
    } catch (e) { toast(e.message, 'err'); }
  });
};
