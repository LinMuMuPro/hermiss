/* pages/chat.js - local Hermiss test chat window */

window.Pages.chat = async function(el) {
  const escapeHtml = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  const formatTime = (value) => {
    if (!value) return '';
    const raw = Number(value);
    const date = Number.isFinite(raw) ? new Date(raw < 100000000000 ? raw * 1000 : raw) : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', { hour12: false });
  };

  const formatState = (data) => {
    const state = data?.state || null;
    const base = data?.state_base || data?.base || null;
    if (!state && !base) {
      return '<div class="empty compact">\u6682\u65e0\u7528\u6237\u72b6\u6001\u5e95\u5ea7</div>';
    }

    const FIELD_LABELS = {
      summary: '\u6458\u8981',
      current_state: '\u5f53\u524d\u72b6\u6001',
      state_at: '\u72b6\u6001\u5224\u65ad\u65f6\u95f4',
      relationship_mood: '\u5173\u7cfb\u6c1b\u56f4',
      caution: '\u6ce8\u610f\u4e8b\u9879',
      recent_context: '\u6700\u8fd1\u4e0a\u4e0b\u6587',
      user_state: '\u7528\u6237\u72b6\u6001',
      checkin_reason: '\u56de\u8bbf\u539f\u56e0',
      current_activity: '\u5f53\u524d\u6d3b\u52a8',
      activity: '\u6d3b\u52a8',
      location: '\u4f4d\u7f6e',
      mood: '\u60c5\u7eea',
      intent: '\u610f\u56fe',
      focus: '\u5173\u6ce8\u70b9',
      availability: '\u53ef\u7528\u72b6\u6001',
      unavailable: '\u662f\u5426\u4e0d\u4fbf\u770b\u624b\u673a',
      expected_duration: '\u9884\u8ba1\u6301\u7eed',
      expected_minutes: '\u9884\u8ba1\u5206\u949f',
      started_at: '\u5f00\u59cb\u65f6\u95f4',
      updated_at: '\u66f4\u65b0\u65f6\u95f4',
      last_user_message: '\u6700\u8fd1\u7528\u6237\u6d88\u606f',
      source_msg: '\u6765\u6e90\u6d88\u606f',
      confidence: '\u7f6e\u4fe1\u5ea6',
      followup_hint: '\u56de\u8bbf\u63d0\u793a',
      next_checkin: '\u4e0b\u6b21\u56de\u8bbf',
      notes: '\u5907\u6ce8',
    };

    const stringifyValue = (value) => {
      if (value == null || value === '') return '';
      if (typeof value === 'boolean') return value ? '\u662f' : '\u5426';
      if (typeof value === 'object') {
        if (Array.isArray(value)) return value.map(stringifyValue).filter(Boolean).join('\u3001');
        return Object.entries(value)
          .map(([key, val]) => `${FIELD_LABELS[key] || key}: ${stringifyValue(val)}`)
          .filter(item => !item.endsWith(': '))
          .join('\uff1b');
      }
      return String(value);
    };
    const displayValueFor = (key, value) => {
      if (key === 'state_at' || key === 'updated_at' || key === 'started_at' || key === 'next_checkin') {
        return formatTime(value);
      }
      return stringifyValue(value);
    };

    const rows = [];
    if (state) {
      const text = state.text || state.current_activity || state.activity || '\u77ed\u671f\u72b6\u6001';
      const source = state.source_msg || state.last_user_message || '';
      rows.push(`<div class="short-state-line primary"><span class="short-state-dot active"></span><strong>state\uff08\u5f53\u524d\u5224\u65ad\uff09</strong><em>${escapeHtml(text)}</em></div>`);
      if (source) rows.push(`<div class="short-state-line"><strong>source_msg\uff08\u89e6\u53d1\u6d88\u606f\uff09</strong><em>${escapeHtml(source)}</em></div>`);
      if (state.expected_minutes != null) rows.push(`<div class="short-state-line"><strong>expected_minutes\uff08\u9884\u8ba1\u5206\u949f\uff09</strong><em>${escapeHtml(state.expected_minutes)} \u5206\u949f</em></div>`);
      rows.push(`<div class="short-state-line"><strong>available\uff08\u662f\u5426\u65b9\u4fbf\u804a\u5929\uff09</strong><em>${state.unavailable ? '\u53ef\u80fd\u4e0d\u65b9\u4fbf' : '\u53ef\u4ee5\u6b63\u5e38\u804a\u5929'}</em></div>`);
    }

    if (base && typeof base === 'object') {
      const preferredKeys = ['current_state', 'summary', 'state_at', 'relationship_mood', 'recent_emotion', 'caution', 'updated_at'];
      const entries = [
        ...preferredKeys.filter(key => Object.prototype.hasOwnProperty.call(base, key)).map(key => [key, base[key]]),
        ...Object.entries(base).filter(([key]) => !preferredKeys.includes(key)),
      ];
      entries.forEach(([key, value]) => {
        const displayValue = displayValueFor(key, value);
        if (!displayValue) return;
        const label = FIELD_LABELS[key] || '\u81ea\u5b9a\u4e49\u5b57\u6bb5';
        rows.push(`<div class="short-state-line"><strong>${escapeHtml(key)}\uff08${escapeHtml(label)}\uff09</strong><em>${escapeHtml(displayValue)}</em></div>`);
      });
    }

    return `<div class="short-state-compact">${rows.slice(0, 8).join('')}</div>`;
  };

  const renderMessages = (messages) => {
    if (!messages.length) {
      return '<div class="chat-empty"><h3>暂无对话</h3><p>可以在这里测试 Hermiss，历史会从本地容器读取。</p></div>';
    }
    return messages.map(msg => {
      const role = msg.role === 'user' ? 'user' : 'assistant';
      const speaker = role === 'user' ? '你' : 'Hermiss';
      return `
        <div class="chat-message ${role}">
          <div class="chat-bubble">
            <div class="chat-meta"><span class="chat-speaker">${speaker}</span><span>${escapeHtml(formatTime(msg.timestamp))}</span></div>
            <div class="chat-text">${escapeHtml(msg.content || '').replace(/\n/g, '<br>')}</div>
            <button class="chat-copy" data-copy="${escapeHtml(msg.content || '')}">复制</button>
          </div>
        </div>
      `;
    }).join('');
  };

  let history = { messages: [] };
  let shortState = null;
  const [historyResult, shortStateResult] = await Promise.allSettled([
    api('/api/chat/history?limit=80'),
    api('/api/chat/short-state'),
  ]);
  if (historyResult.status === 'fulfilled') history = historyResult.value;
  if (shortStateResult.status === 'fulfilled') shortState = shortStateResult.value;

  el.innerHTML = `
    <div class="page-head chat-page-head">
      <div class="chat-title-row">
        <h2>\u804a\u5929\u7a97\u53e3</h2>
        <p class="page-subtitle">\u5728\u9762\u677f\u91cc\u76f4\u63a5\u548c Hermiss \u6d4b\u8bd5\u5bf9\u8bdd\uff0c\u540c\u65f6\u67e5\u770b\u5f53\u524d\u7528\u6237\u72b6\u6001\u5e95\u5ea7\u3002</p>
      </div>
      <button class="btn btn-sm" id="btn-chat-refresh">\u5237\u65b0</button>
    </div>
    <div class="settings-single-column">
      <div class="short-state-card">
        <div class="short-state-head">
          <div>
            <h3>用户状态底座</h3>
            <p>用于当前对话连续性和主动回访判断，不等同长期记忆。</p>
          </div>
        </div>
        <div class="short-state-body">${formatState(shortState)}</div>
      </div>
      <div class="card chat-panel">
        <div class="chat-history" id="chat-history">${renderMessages(history.messages || [])}</div>
        <div class="chat-compose">
          <textarea id="chat-input" rows="3" placeholder="输入要测试的话，Enter 发送，Shift+Enter 换行"></textarea>
          <button class="btn btn-primary" id="btn-chat-send">发送</button>
        </div>
        <p class="form-hint">这里走本地容器，不影响微信绑定；回复较慢时说明正在等待模型返回。</p>
      </div>
    </div>
  `;

  const scrollBottom = () => {
    const box = document.getElementById('chat-history');
    if (box) box.scrollTop = box.scrollHeight;
  };
  scrollBottom();

  document.getElementById('btn-chat-refresh')?.addEventListener('click', () => navigate('chat'));
  document.querySelectorAll('.chat-copy').forEach(btn => {
    btn.addEventListener('click', async function() {
      await navigator.clipboard.writeText(this.dataset.copy || '');
      toast('已复制', 'ok');
    });
  });

  const send = async () => {
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('btn-chat-send');
    const message = input.value.trim();
    if (!message) return;
    btn.disabled = true;
    btn.textContent = '回复中...';
    try {
      const box = document.getElementById('chat-history');
      box.insertAdjacentHTML('beforeend', renderMessages([{ role: 'user', content: message, timestamp: new Date().toISOString() }]));
      box.insertAdjacentHTML('beforeend', '<div class="chat-thinking" id="chat-thinking">Hermiss 正在回复...</div>');
      input.value = '';
      scrollBottom();
      const result = await api('/api/chat/send', { method: 'POST', body: JSON.stringify({ message }) });
      document.getElementById('chat-thinking')?.remove();
      const messages = result.history?.messages || [];
      box.innerHTML = renderMessages(messages);
      try {
        shortState = await api('/api/chat/short-state');
        document.querySelector('.short-state-body').innerHTML = formatState(shortState);
      } catch (_) {}
      scrollBottom();
    } catch (e) {
      document.getElementById('chat-thinking')?.remove();
      toast(e.message || '发送失败', 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '发送';
    }
  };

  document.getElementById('btn-chat-send')?.addEventListener('click', send);
  document.getElementById('chat-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
};
