/* ── pages/persona.js ── */

document.addEventListener('click', async e => {
  const btn = e.target.closest('.btn-tpl');
  if (!btn) return;
  const tplId = btn.dataset.tpl;
  const original = btn.textContent;
  try {
    btn.disabled = true;
    btn.textContent = '应用中...';
    await api(`/api/persona/apply-template/${tplId}`, { method: 'POST' });
    btn.textContent = '重启中...';
    showRestartOverlay('容器重启中，约 5-10 秒...');
    await waitForRestart();
    hideRestartOverlay();
    toast('模板已应用，容器重启完成', 'ok');
    navigate('persona');
  } catch (err) {
    hideRestartOverlay();
    toast(err.message, 'err');
    btn.disabled = false;
    btn.textContent = original;
  }
});

window.Pages.persona = async function(el) {
  let persona, templates;
  try { persona = await api('/api/persona/current'); } catch (_) { persona = null; }
  try { templates = await api('/api/persona/templates'); } catch (_) { templates = null; }

  el.innerHTML = `
    <h2>人设管理</h2>

    <div class="persona-layout">
      <div class="card persona-card">
        <div class="card-head">
          <div>
            <h3>当前人设文件</h3>
            <p class="form-hint">保存后会写入容器并重启。高级用户也可以直接编辑原始 Markdown。</p>
          </div>
        </div>

        <div class="form-group">
          <label>SOUL.md（Bot 人设）</label>
          <textarea id="persona-soul" rows="14" placeholder="在这里编写 Bot 的系统人设...">${escapeHtml(persona ? persona.soul || '' : '')}</textarea>
        </div>

        <div class="form-group">
          <label>USER.md（用户人设）</label>
          <textarea id="persona-user" rows="6" placeholder="在这里描述用户信息...">${escapeHtml(persona ? persona.user || '' : '')}</textarea>
        </div>

        <div class="btn-group">
          <button class="btn btn-primary" id="btn-save-persona">保存人设</button>
          ${templates && templates.templates ? templates.templates.map(t =>
            `<button class="btn btn-sm btn-tpl" data-tpl="${escapeHtml(t.id)}">${escapeHtml(t.name)}</button>`
          ).join('') : ''}
        </div>
        <p class="form-hint">保存或选择模板后会重启容器（约 5-10 秒）。</p>
      </div>

      <div class="card persona-card persona-generate-card">
        <div class="card-head">
          <div>
            <h3>AI 生成人设</h3>
            <p class="form-hint">填写几个关键词，生成草稿后先预览，确认后再保存到 SOUL.md / USER.md。</p>
          </div>
        </div>

        <div class="persona-form-grid">
          <div class="form-group">
            <label>角色名称</label>
            <input id="gen-name" placeholder="例如：小栀">
          </div>
          <div class="form-group">
            <label>关系定位</label>
            <input id="gen-relationship" value="虚拟恋人">
          </div>
          <div class="form-group">
            <label>性格关键词</label>
            <input id="gen-personality" placeholder="温柔、有主见、嘴硬、爱撒娇">
          </div>
          <div class="form-group">
            <label>说话风格</label>
            <input id="gen-speaking-style" placeholder="短句、自然、像熟人聊天、不说教">
          </div>
          <div class="form-group">
            <label>背景设定</label>
            <input id="gen-background" placeholder="可选，例如职业、生活习惯、兴趣">
          </div>
          <div class="form-group">
            <label>边界/禁忌</label>
            <input id="gen-boundaries" placeholder="不能编造现实动作；不能像客服或 AI">
          </div>
        </div>

        <div class="form-group">
          <label>用户信息</label>
          <textarea id="gen-user-info" rows="3" placeholder="例如：用户喜欢什么、讨厌什么、希望怎样相处"></textarea>
        </div>
        <div class="form-group">
          <label>额外要求</label>
          <textarea id="gen-extra" rows="3" placeholder="可选：补充你希望加入的人设细节"></textarea>
        </div>

        <div class="btn-group">
          <button class="btn btn-primary" id="btn-generate-persona">AI 生成草稿</button>
          <button class="btn" id="btn-use-generated" disabled>使用生成结果填入编辑区</button>
        </div>
      </div>

      <div class="card persona-card persona-preview-card">
        <h3>生成结果预览</h3>
        <p class="form-hint">这里不会自动覆盖当前人设。确认满意后，先填入编辑区，再点击保存。</p>
        <div class="form-group">
          <label>SOUL.md 预览</label>
          <textarea id="generated-soul" rows="12" placeholder="生成后显示 Bot 人设..."></textarea>
        </div>
        <div class="form-group">
          <label>USER.md 预览</label>
          <textarea id="generated-user" rows="5" placeholder="生成后显示用户信息..."></textarea>
        </div>
      </div>
    </div>
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
      navigate('persona');
    } catch (e) {
      hideRestartOverlay();
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  document.getElementById('btn-generate-persona')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-generate-persona');
    const original = btn.textContent;
    try {
      btn.disabled = true;
      btn.textContent = '生成中...';
      const result = await api('/api/persona/generate', {
        method: 'POST',
        body: JSON.stringify({
          name: valueOf('gen-name'),
          relationship: valueOf('gen-relationship'),
          personality: valueOf('gen-personality'),
          speaking_style: valueOf('gen-speaking-style'),
          background: valueOf('gen-background'),
          boundaries: valueOf('gen-boundaries'),
          user_info: valueOf('gen-user-info'),
          extra: valueOf('gen-extra')
        })
      });
      document.getElementById('generated-soul').value = result.soul || '';
      document.getElementById('generated-user').value = result.user || '';
      document.getElementById('btn-use-generated').disabled = false;
      toast(`已生成草稿：${result.provider || ''}/${result.model || ''}`, 'ok');
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });

  document.getElementById('btn-use-generated')?.addEventListener('click', () => {
    const soul = document.getElementById('generated-soul').value;
    const user = document.getElementById('generated-user').value;
    if (!soul.trim()) {
      toast('还没有可用的生成结果', 'err');
      return;
    }
    document.getElementById('persona-soul').value = soul;
    document.getElementById('persona-user').value = user;
    toast('已填入编辑区，确认后点击保存人设', 'ok');
  });

  document.getElementById('btn-save-persona')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-save-persona');
    saveWithRestart(btn, async () => {
      await api('/api/persona/update', {
        method: 'POST',
        body: JSON.stringify({
          soul: document.getElementById('persona-soul').value,
          user: document.getElementById('persona-user').value
        })
      });
    }, '保存人设', '人设已保存，容器重启完成');
  });
};

function valueOf(id) {
  return document.getElementById(id)?.value?.trim() || '';
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}
