/* ── pages/settings.js ──
   v6.0 — 保存后显示重启遮罩 + 轮询等待容器就绪 */

window.Pages.settings = async function(el) {
  let model, vision, msgs, waitCfg, wechatStatus;
  try { model = await api('/api/settings/model'); } catch (_) { model = null; }
  try { vision = await api('/api/settings/vision'); } catch (_) { vision = null; }
  try { msgs = await api('/api/settings/messages'); } catch (_) { msgs = null; }
  try { waitCfg = await api('/api/settings/message-wait'); } catch (_) { waitCfg = null; }
  try { wechatStatus = await api('/api/wechat/status'); } catch (_) { wechatStatus = null; }

  const modelConfigured = model && (model.provider || model.model) && model.has_key;
  const visionConfigured = vision && (vision.provider || vision.model) && vision.has_key;
  const wechatBound = !!(wechatStatus && wechatStatus.bound);

  el.innerHTML = `
    <h2>设置</h2>

    <!-- 微信扫码绑定 -->
    <div class="card settings-wide" id="card-wechat" style="padding:16px">
      <div class="card-head">
        <div>
          <h3>微信绑定</h3>
          <p class="form-hint">只保留扫码绑定。扫码确认后会自动写入容器配置并重启。</p>
        </div>
        <span class="badge ${wechatBound ? 'badge-ok' : 'badge-neutral'}">${wechatBound ? '已绑定' : '未绑定'}</span>
      </div>
      ${wechatStatus && wechatStatus.account_id ? `<div class="card-row"><span class="card-label">Account ID</span><span class="card-value mono">${wechatStatus.account_id}</span></div>` : ''}
      ${!wechatBound ? `
        <div class="wechat-qr-panel">
          <div>
            <button class="btn btn-primary" id="btn-settings-qr">生成微信二维码</button>
            <p class="form-hint">使用微信扫码并在手机端确认，确认后面板会等待容器重启完成。</p>
          </div>
          <div id="settings-qr-area" class="wechat-qr-area" style="display:none">
            <div id="settings-qr-status" class="form-hint">等待生成二维码...</div>
            <div id="settings-qr-code" class="wechat-qr-code"></div>
          </div>
        </div>
      ` : `
        <div class="btn-group" style="margin-top:12px">
          <button class="btn" id="btn-settings-connection-test">连接测试</button>
          <button class="btn btn-danger" id="btn-settings-unbind">解绑微信</button>
        </div>
        <div class="card compact-card" id="settings-conn-test-card" style="display:none;margin-top:12px">
          <h3>连接测试</h3>
          <div id="settings-conn-test-result"></div>
        </div>
      `}
    </div>

    <!-- 主模型 -->
    <div class="card" id="card-model" style="${modelConfigured
      ? 'border:2px solid var(--accent);border-radius:8px;position:relative;padding:16px'
      : 'padding:16px'}">
      ${modelConfigured ? `<span style="position:absolute;top:8px;right:12px;font-size:.75rem;color:var(--accent);font-weight:600">已配置</span>` : ''}
      <h3>主模型配置</h3>
      <div id="model-status" style="${!modelConfigured ? 'display:none' : ''}">
        <span style="color:var(--accent);font-weight:600">${model?.provider || ''}</span>
        <span style="margin:0 4px;color:var(--text3)">/</span>
        <span>${model?.model || ''}</span>
        ${model?.has_key ? `<span class="key-badge">Key&#10003;</span>` : ''}
        <button class="btn btn-sm" id="btn-model-edit" style="margin-left:12px">修改</button>
        <button class="btn btn-sm" id="btn-model-key">更新 API Key</button>
      </div>
      <div id="model-form" style="${modelConfigured ? 'display:none' : ''}">
        <div class="form-group">
          <label>Provider</label>
          <select id="set-provider">
            <option value="deepseek" data-url="https://api.deepseek.com/v1" data-model="deepseek-v4-flash">DeepSeek</option>
            <option value="openai" data-url="https://api.openai.com/v1" data-model="gpt-4o">OpenAI</option>
            <option value="custom" data-url="" data-model="">自定义 / 中转站</option>
          </select>
        </div>
        <div class="form-group">
          <label>Base URL</label>
          <input id="set-base-url" value="https://api.deepseek.com/v1" placeholder="https://api.openai.com/v1">
        </div>
        <div class="form-group">
          <label>Model</label>
          <input id="set-model" value="deepseek-v4-flash" placeholder="deepseek-v4-flash / gpt-4o">
        </div>
        <div class="form-group">
          <label>API Key</label>
          <input id="set-api-key" type="password" placeholder="sk-xxxxxxxx">
        </div>
        <div class="btn-group">
          <button class="btn" id="btn-test-model">测试模型连接</button>
          <button class="btn btn-primary" id="btn-save-model">${modelConfigured ? '修改模型配置' : '保存模型配置'}</button>
        </div>
        <p id="model-test-result" class="form-hint"></p>
        <p class="form-hint">保存后容器重启（约5-10秒），请稍后刷新</p>
      </div>
    </div>

    <!-- 视觉模型 -->
    <div class="card" style="${visionConfigured
      ? 'border:2px solid var(--accent);border-radius:8px;position:relative;padding:16px'
      : 'padding:16px'}">
      ${visionConfigured ? `<span style="position:absolute;top:8px;right:12px;font-size:.75rem;color:var(--accent);font-weight:600">已配置</span>` : ''}
      <h3>视觉模型配置</h3>
      <div id="vision-status" style="${!visionConfigured ? 'display:none' : ''}">
        <span style="color:var(--accent);font-weight:600">${vision?.provider || ''}</span>
        <span style="margin:0 4px;color:var(--text3)">/</span>
        <span>${vision?.model || ''}</span>
        ${vision?.has_key ? `<span class="key-badge">Key&#10003;</span>` : ''}
        <button class="btn btn-sm" id="btn-vision-edit" style="margin-left:12px">修改</button>
      </div>
      <div id="vision-form" style="${visionConfigured ? 'display:none' : ''}">
        <div class="form-group">
          <label>Provider</label>
          <input id="set-vision-provider" value="${vision ? vision.provider || '' : ''}" placeholder="deepseek">
        </div>
        <div class="form-group">
          <label>Model</label>
          <input id="set-vision-model" value="${vision ? vision.model || '' : ''}" placeholder="deepseek-v4-flash">
        </div>
        <div class="form-group">
          <label>API Key（${vision?.has_key ? '已设置，留空保持不变' : '可选'}）</label>
          <input id="set-vision-key" type="password" placeholder="${vision?.has_key ? '留空保持不变' : '输入 key（可选，默认用主模型 key）'}">
        </div>
        <button class="btn btn-primary" id="btn-save-vision">${visionConfigured ? '修改视觉模型' : '保存视觉模型'}</button>
        <p class="form-hint">当模型需要看图时使用此配置。保存后容器重启（约5-10秒）</p>
      </div>
    </div>

    <!-- 多行分条 -->
    <div class="card" style="padding:16px">
      <h3>多行消息分条</h3>
      <div class="theme-toggle" style="margin-bottom:${msgs && msgs.enabled ? '12px' : '0'}">
        <span>强制按换行分条发送</span>
        <label class="theme-switch">
          <input type="checkbox" id="set-msg-enabled" ${msgs && msgs.enabled ? 'checked' : ''}>
          <span class="theme-slider"></span>
        </label>
      </div>
      <div class="form-group" id="msg-delay-group" style="${msgs && msgs.enabled ? '' : 'display:none'}">
        <label>分条延迟（秒）</label>
        <input id="set-msg-delay" type="number" min="0" max="30" step="0.5" value="${msgs ? msgs.delay_seconds || 0 : 0}">
      </div>
      <p class="form-hint">开启后，包含换行的回复会尽量按行拆成多条微信气泡；关闭后，长段落/列表会尽量合并为一条，但短口语多行仍可能被运行时自动拆成自然气泡。保存后容器重启（约5-10秒）。</p>
    </div>

    <!-- 回复等待 -->
    <div class="card" style="padding:16px">
      <h3>回复等待</h3>
      <p class="form-hint" style="margin-bottom:12px">
        用于把用户短时间内连续发送的多句话合并成一轮，减少 LLM 分开回复造成的割裂感。保存后容器重启（约5-10秒）。
      </p>
      <div class="form-group">
        <label>回复等待（秒）</label>
        <input id="set-wait-seconds" type="number" min="0" max="30" step="0.5" value="${waitCfg ? waitCfg.wait_seconds || 6 : 6}">
        <p class="form-hint">统一控制普通连发、长文本拆分和回复中追发的等待窗口。推荐 5-8 秒。</p>
      </div>
      <button class="btn btn-primary" id="btn-save-message-wait">保存回复等待</button>
    </div>

    <!-- 主动回复 -->
    <div class="card" style="padding:16px">
      <h3>主动回复</h3>
      <p class="form-hint" style="margin-bottom:12px">
        控制是否允许 Hermiss 在合适的时间根据上下文主动发送回复。开关修改后会自动保存并重启容器（约5-10秒）。
      </p>
      <div class="theme-toggle" style="margin:12px 0">
        <span>开启主动回复</span>
        <label class="theme-switch">
          <input type="checkbox" id="set-proactive-checkin" ${!waitCfg || waitCfg.proactive_checkin_enabled ? 'checked' : ''}>
          <span class="theme-slider"></span>
        </label>
      </div>
      <p class="form-hint">关闭后，不会主动发送回复。</p>
    </div>

    <!-- 记忆插件开关 -->
    <div class=\"card\" style=\"padding:16px\">
      <h3>记忆插件</h3>
      <p style=\"font-size:.82rem;color:var(--text2);margin-bottom:12px\">
        开启后对话会分析存储记忆，关闭后只聊天不记任何内容。
      </p>
      <div class=\"theme-toggle\">
        <span id=\"mem-label\">记忆插件</span>
        <label class=\"theme-switch\">
          <input type=\"checkbox\" id=\"set-mem-enabled\">
          <span class=\"theme-slider\"></span>
        </label>
      </div>
    </div>

    <!-- 重置 -->
    <div class="card" style="padding:16px">
      <h3>重置 Profile</h3>
      <p style="font-size:.82rem;color:var(--text2);margin-bottom:12px">
        清空人设、记忆，恢复到初始状态。SOUL.md 会被备份为 SOUL_backup.md。
      </p>
      <button class="btn btn-danger" id="btn-reset">重置 Profile</button>
    </div>
  `;

  // ── 用已保存的配置初始化表单 ──
  if (model) {
    document.getElementById('set-provider').value = model.provider || 'deepseek';
    document.getElementById('set-model').value = model.model || 'deepseek-v4-flash';
    document.getElementById('set-base-url').value = model.base_url || 'https://api.deepseek.com/v1';
  }
  if (vision) {
    const vp = document.getElementById('set-vision-provider');
    const vm = document.getElementById('set-vision-model');
    if (vp) vp.value = vision.provider || '';
    if (vm) vm.value = vision.model || '';
  }

  // ── Helper: save → restart → wait ──
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
      navigate('settings');
    } catch (e) {
      hideRestartOverlay();
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  // ── WeChat QR binding (settings page only) ──
  let settingsQrPolling = null;
  let settingsQrConfirmed = false;

  document.getElementById('btn-settings-qr')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-settings-qr');
    const area = document.getElementById('settings-qr-area');
    const statusEl = document.getElementById('settings-qr-status');
    const qrEl = document.getElementById('settings-qr-code');

    btn.disabled = true;
    btn.textContent = '生成中...';
    try {
      const data = await api('/api/wechat/qr', { method: 'POST' });
      qrEl.innerHTML = `<img src="${data.qr_image}" alt="微信二维码">`;
      area.style.display = '';
      statusEl.textContent = '等待扫码...';
      btn.style.display = 'none';

      if (settingsQrPolling) clearInterval(settingsQrPolling);
      settingsQrPolling = setInterval(async () => {
        try {
          const result = await api(`/api/wechat/qr/${data.qr_id}`);
          if (result.status === 'scaned') {
            statusEl.textContent = '已扫码，请在微信中确认...';
          } else if (result.status === 'confirmed') {
            if (settingsQrConfirmed) return;
            settingsQrConfirmed = true;
            clearInterval(settingsQrPolling);
            settingsQrPolling = null;
            statusEl.textContent = '绑定成功，容器重启中...';
            showRestartOverlay('容器重启中，约 5-10 秒...');
            await waitForRestart();
            hideRestartOverlay();
            toast('微信已绑定，容器已就绪', 'ok');
            navigate('settings');
          } else if (result.status === 'expired') {
            clearInterval(settingsQrPolling);
            settingsQrPolling = null;
            statusEl.textContent = '二维码已过期';
            btn.style.display = '';
            btn.disabled = false;
            btn.textContent = '重新生成二维码';
          }
        } catch (_) {}
      }, 2000);
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = '生成微信二维码';
    }
  });


  document.getElementById('btn-settings-connection-test')?.addEventListener('click', async () => {
    try {
      const data = await api('/api/wechat/connection-test');
      const card = document.getElementById('settings-conn-test-card');
      card.style.display = '';
      document.getElementById('settings-conn-test-result').innerHTML = `
        <div class="inline-status">
          <span class="dot ${data.connected ? 'dot-ok' : 'dot-err'}"></span>
          <span>${data.connected ? '已连接' : '未连接'}</span>
          <span class="badge badge-neutral">${data.state || 'unknown'}</span>
        </div>
        <div class="log-view">${data.log || '(无日志)'}</div>`;
    } catch (e) { toast(e.message, 'err'); }
  });

  document.getElementById('btn-settings-unbind')?.addEventListener('click', async () => {
    const ok = await dialogConfirm('确定解绑微信？');
    if (!ok) return;
    try {
      await api('/api/wechat/unbind', { method: 'POST' });
      toast('已解绑微信', 'ok');
      navigate('settings');
    } catch (e) { toast(e.message, 'err'); }
  });

  // ── Edit toggle ──
  document.getElementById('btn-model-edit')?.addEventListener('click', () => {
    document.getElementById('model-status').style.display = 'none';
    document.getElementById('model-form').style.display = '';
  });
  document.getElementById('btn-vision-edit')?.addEventListener('click', () => {
    document.getElementById('vision-status').style.display = 'none';
    document.getElementById('vision-form').style.display = '';
  });

  // ── Save model ──
  // Provider 下拉切换：自动填充 Base URL 和 Model
  document.getElementById('set-provider')?.addEventListener('change', function() {
    const opt = this.options[this.selectedIndex];
    const url = opt.dataset.url;
    const mdl = opt.dataset.model;
    if (url) document.getElementById('set-base-url').value = url;
    if (mdl) document.getElementById('set-model').value = mdl;
  });

  function getModelFormBody() {
    const body = {
      provider: document.getElementById('set-provider').value.trim(),
      model: document.getElementById('set-model').value.trim()
    };
    const key = document.getElementById('set-api-key').value.trim();
    const baseUrl = document.getElementById('set-base-url').value.trim();
    if (key) body.api_key = key;
    if (baseUrl) body.base_url = baseUrl;
    return body;
  }

  document.getElementById('btn-test-model')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-test-model');
    const result = document.getElementById('model-test-result');
    try {
      btn.disabled = true;
      btn.textContent = '测试中...';
      if (result) {
        result.textContent = '正在请求模型接口...';
        result.style.color = 'var(--text3)';
      }
      const data = await api('/api/settings/model/test', {
        method: 'POST',
        body: JSON.stringify(getModelFormBody())
      });
      if (result) {
        result.textContent = `连接成功：${data.provider || ''} / ${data.model || ''}`;
        result.style.color = 'var(--success)';
      }
      toast('模型连接测试通过', 'ok');
    } catch (e) {
      if (result) {
        result.textContent = e.message || '模型连接测试失败';
        result.style.color = 'var(--danger)';
      }
      toast(e.message || '模型连接测试失败', 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '测试模型连接';
    }
  });

  document.getElementById('btn-save-model')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-save-model');
    const label = modelConfigured ? '修改模型配置' : '保存模型配置';
    saveWithRestart(btn, async () => {
      const body = getModelFormBody();
      await api('/api/settings/model', { method: 'POST', body: JSON.stringify(body) });
    }, label, '模型配置已保存，容器重启完成');
  });

  // ── Save vision ──
  document.getElementById('btn-save-vision')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-save-vision');
    const label = visionConfigured ? '修改视觉模型' : '保存视觉模型';
    saveWithRestart(btn, async () => {
      const body = {
        provider: document.getElementById('set-vision-provider').value.trim(),
        model: document.getElementById('set-vision-model').value.trim(),
      };
      const key = document.getElementById('set-vision-key').value.trim();
      if (key) body.api_key = key;
      await api('/api/settings/vision', { method: 'POST', body: JSON.stringify(body) });
    }, label, '视觉模型已保存，容器重启完成');
  });

  // ── Messages: auto-save（轻量，不锁界面，但加遮罩）──
  let _msgSaving = false;
  async function saveMessages() {
    if (_msgSaving) return;
    _msgSaving = true;
    try {
      await api('/api/settings/messages', {
        method: 'POST',
        body: JSON.stringify({
          enabled: document.getElementById('set-msg-enabled').checked,
          delay_seconds: Number(document.getElementById('set-msg-delay').value) || 0
        })
      });
      showRestartOverlay('容器重启中，约 5-10 秒...');
      await waitForRestart();
      hideRestartOverlay();
      toast('分条配置已生效，容器重启完成', 'ok');
    } catch (e) {
      hideRestartOverlay();
      toast(e.message, 'err');
    }
    _msgSaving = false;
  }

  document.getElementById('set-msg-enabled')?.addEventListener('change', () => {
    const delayGroup = document.getElementById('msg-delay-group');
    if (delayGroup) {
      delayGroup.style.display = document.getElementById('set-msg-enabled').checked ? '' : 'none';
    }
    saveMessages();
  });

  let msgDelayTimer;
  document.getElementById('set-msg-delay')?.addEventListener('input', () => {
    clearTimeout(msgDelayTimer);
    msgDelayTimer = setTimeout(saveMessages, 800);
  });

  // ── Message wait ──
  document.getElementById('btn-save-message-wait')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-save-message-wait');
    saveWithRestart(btn, async () => {
      await api('/api/settings/message-wait', {
        method: 'POST',
        body: JSON.stringify({
          wait_seconds: Number(document.getElementById('set-wait-seconds').value),
          proactive_checkin_enabled: !!(waitCfg && waitCfg.proactive_checkin_enabled),
        })
      });
    }, '保存回复等待', '回复等待时间已保存，容器重启完成');
  });

  document.getElementById('set-proactive-checkin')?.addEventListener('change', async function() {
    const enabled = this.checked;
    try {
      await api('/api/settings/message-wait', {
        method: 'POST',
        body: JSON.stringify({
          wait_seconds: Number(waitCfg && waitCfg.wait_seconds ? waitCfg.wait_seconds : 6),
          proactive_checkin_enabled: enabled,
        })
      });
      showRestartOverlay('容器重启中，约 5-10 秒...');
      await waitForRestart();
      hideRestartOverlay();
      toast(enabled ? '主动回复已开启' : '主动回复已关闭', 'ok');
      navigate('settings');
    } catch (e) {
      hideRestartOverlay();
      toast(e.message, 'err');
      this.checked = !enabled;
    }
  });


  // ── Save API key ──
  document.getElementById('btn-model-key')?.addEventListener('click', async () => {
    const key = (await dialogPrompt('输入新的 API Key', ''))?.trim();
    if (!key) return toast('请输入 API Key', 'err');
    const btn = document.getElementById('btn-model-key');
    const label = '更新 API Key';
    saveWithRestart(btn, async () => {
      await api('/api/settings/api-key', { method: 'POST', body: JSON.stringify({ api_key: key }) });
    }, label, 'API Key 已更新，容器重启完成');
  });

  // ── Reset ──
  document.getElementById('btn-reset')?.addEventListener('click', async () => {
    const ok = await dialogConfirm('确定重置 Profile？人设和记忆将被清空。SOUL.md 会备份。');
    if (!ok) return;
    const btn = document.getElementById('btn-reset');
    saveWithRestart(btn, async () => {
      await api('/api/settings/reset', { method: 'POST' });
    }, '重置 Profile', 'Profile 已重置');
  });

  // ── 记忆插件开关 ──
  try {
    const memStatus = await api('/api/settings/memory-plugin');
    const memCheck = document.getElementById('set-mem-enabled');
    if (memCheck) memCheck.checked = memStatus.enabled;
  } catch(_) {}

  document.getElementById('set-mem-enabled')?.addEventListener('change', async function() {
    const enabled = this.checked;
    try {
      await api('/api/settings/memory-plugin', { method: 'POST', body: JSON.stringify({ enabled }) });
      showRestartOverlay('容器重启中，约 5-10 秒...');
      await waitForRestart();
      hideRestartOverlay();
      toast(enabled ? '记忆插件已开启' : '记忆插件已关闭', 'ok');
    } catch(e) {
      hideRestartOverlay();
      toast(e.message, 'err');
      this.checked = !enabled;
    }
  });
};
