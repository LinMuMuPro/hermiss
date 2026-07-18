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

  const state = {
    soul: persona ? persona.soul || '' : '',
    user: persona ? persona.user || '' : '',
    generatedSoul: '',
    generatedUser: ''
  };

  function renderMain() {
    el.innerHTML = `
      <h2>人设管理</h2>

      <div class="persona-layout persona-layout-three">
        <div class="card persona-card persona-editor-card persona-soul-card">
          <div class="card-head">
            <div>
              <h3>SOUL.md</h3>
              <p class="form-hint">左侧只编辑 Bot 人设，保存后会写入容器并重启。</p>
            </div>
          </div>

          <div class="form-group persona-soul-group">
            <label>Bot 人设</label>
            <textarea id="persona-soul" rows="24" placeholder="在这里编写 Bot 的系统人设...">${escapeHtml(state.soul)}</textarea>
          </div>
        </div>

        <div class="card persona-card persona-editor-card persona-user-card">
          <div class="card-head">
            <div>
              <h3>USER.md</h3>
              <p class="form-hint">右侧只编辑用户信息，不再放到底部。</p>
            </div>
          </div>

          <div class="form-group persona-soul-group">
            <label>用户信息</label>
            <textarea id="persona-user" rows="24" placeholder="在这里描述用户信息...">${escapeHtml(state.user)}</textarea>
          </div>
        </div>

        <aside class="card persona-card persona-actions-card">
          <div class="card-head">
            <div>
              <h3>操作</h3>
              <p class="form-hint">这里只保留按钮，需要生成时进入单独页面。</p>
            </div>
          </div>

          <div class="persona-action-stack">
            <button class="btn btn-primary persona-main-action" id="btn-save-persona">保存人设</button>
            <button class="btn persona-main-action" id="btn-open-generate">AI 生成人设</button>
            <button class="btn persona-main-action" id="btn-use-generated" ${state.generatedSoul ? '' : 'disabled'}>使用生成结果</button>
          </div>

          <div class="persona-action-section">
            <h4>模板</h4>
            <div class="persona-template-list">
              ${templates && templates.templates ? templates.templates.map(t =>
                `<button class="btn btn-sm btn-tpl" data-tpl="${escapeHtml(t.id)}">${escapeHtml(t.name)}</button>`
              ).join('') : '<span class="form-hint">暂无模板</span>'}
            </div>
          </div>

          <div class="persona-action-section">
            <h4>备用操作</h4>
            <div class="persona-reserve-grid">
              <button class="btn btn-sm" type="button" disabled>导入人设</button>
              <button class="btn btn-sm" type="button" disabled>导出人设</button>
              <button class="btn btn-sm" type="button" disabled>恢复备份</button>
              <button class="btn btn-sm" type="button" disabled>版本对比</button>
            </div>
            <p class="form-hint">先预留位置，后续可以接入导入、导出和备份逻辑。</p>
          </div>
        </aside>
      </div>
    `;

    bindMainEvents();
  }

  function renderGenerate() {
    el.innerHTML = `
      <div class="persona-page-head">
        <div>
          <h2>AI 生成人设</h2>
          <p class="form-hint">填写关键词生成草稿，满意后再填入当前人设编辑区。</p>
        </div>
        <button class="btn" id="btn-back-persona">返回人设管理</button>
      </div>

      <div class="persona-generate-layout">
        <div class="card persona-card persona-generate-card">
          <div class="card-head">
            <div>
              <h3>生成条件</h3>
              <p class="form-hint">不用填满，给几个关键点就可以。</p>
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
              <input id="gen-speaking-style" placeholder="短句、自然、像熟人聊天">
            </div>
            <div class="form-group">
              <label>背景设定</label>
              <input id="gen-background" placeholder="可选，例如职业、生活习惯">
            </div>
            <div class="form-group">
              <label>边界/禁忌</label>
              <input id="gen-boundaries" placeholder="不能编造现实动作；不能像客服或 AI">
            </div>
          </div>

          <div class="form-group">
            <label>用户信息</label>
            <textarea id="gen-user-info" rows="4" placeholder="用户喜欢什么、讨厌什么、希望怎样相处"></textarea>
          </div>
          <div class="form-group">
            <label>额外要求</label>
            <textarea id="gen-extra" rows="4" placeholder="补充你希望加入的人设细节"></textarea>
          </div>

          <div class="btn-group">
            <button class="btn btn-primary" id="btn-generate-persona">AI 生成草稿</button>
            <button class="btn" id="btn-generate-use" ${state.generatedSoul ? '' : 'disabled'}>使用结果并返回</button>
          </div>
        </div>

        <div class="card persona-card persona-preview-card">
          <div class="card-head">
            <div>
              <h3>生成结果预览</h3>
              <p class="form-hint">这里的内容可以先检查，再决定是否填入编辑区。</p>
            </div>
          </div>
          <div class="form-group persona-soul-group">
            <label>SOUL.md 预览</label>
            <textarea id="generated-soul" rows="18" placeholder="生成后显示 Bot 人设...">${escapeHtml(state.generatedSoul)}</textarea>
          </div>
          <div class="form-group">
            <label>USER.md 预览</label>
            <textarea id="generated-user" rows="7" placeholder="生成后显示用户信息...">${escapeHtml(state.generatedUser)}</textarea>
          </div>
        </div>
      </div>
    `;

    bindGenerateEvents();
  }

  function snapshotEditor() {
    const soul = document.getElementById('persona-soul');
    const user = document.getElementById('persona-user');
    if (soul) state.soul = soul.value;
    if (user) state.user = user.value;
  }

  function snapshotGenerated() {
    const soul = document.getElementById('generated-soul');
    const user = document.getElementById('generated-user');
    if (soul) state.generatedSoul = soul.value;
    if (user) state.generatedUser = user.value;
  }

  function useGeneratedAndReturn() {
    snapshotGenerated();
    if (!state.generatedSoul.trim()) {
      toast('还没有可用的生成结果', 'err');
      return;
    }
    state.soul = state.generatedSoul;
    state.user = state.generatedUser;
    renderMain();
    toast('已填入编辑区，确认后点击保存人设', 'ok');
  }

  function bindMainEvents() {
    document.getElementById('btn-open-generate')?.addEventListener('click', () => {
      snapshotEditor();
      renderGenerate();
    });

    document.getElementById('btn-use-generated')?.addEventListener('click', () => {
      if (!state.generatedSoul.trim()) {
        toast('还没有可用的生成结果', 'err');
        return;
      }
      state.soul = state.generatedSoul;
      state.user = state.generatedUser;
      renderMain();
      toast('已填入编辑区，确认后点击保存人设', 'ok');
    });

    document.getElementById('btn-save-persona')?.addEventListener('click', () => {
      const btn = document.getElementById('btn-save-persona');
      saveWithRestart(btn, async () => {
        snapshotEditor();
        await api('/api/persona/update', {
          method: 'POST',
          body: JSON.stringify({
            soul: state.soul,
            user: state.user
          })
        });
      }, '保存人设', '人设已保存，容器重启完成');
    });
  }

  function bindGenerateEvents() {
    document.getElementById('btn-back-persona')?.addEventListener('click', () => {
      snapshotGenerated();
      renderMain();
    });

    document.getElementById('btn-generate-use')?.addEventListener('click', useGeneratedAndReturn);

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
        state.generatedSoul = result.soul || '';
        state.generatedUser = result.user || '';
        document.getElementById('generated-soul').value = state.generatedSoul;
        document.getElementById('generated-user').value = state.generatedUser;
        document.getElementById('btn-generate-use').disabled = false;
        toast(`已生成草稿：${result.provider || ''}/${result.model || ''}`, 'ok');
      } catch (err) {
        toast(err.message, 'err');
      } finally {
        btn.disabled = false;
        btn.textContent = original;
      }
    });
  }

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

  renderMain();
};

function valueOf(id) {
  return document.getElementById(id)?.value?.trim() || '';
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}
