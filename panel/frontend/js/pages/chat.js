/* pages/chat.js - local test chat window */

const chatEscapeHtml = value => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

const chatEscapeAttr = value => chatEscapeHtml(value).replace(/'/g, '&#39;');

function chatFormatTime(value) {
  if (!value) return '';
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { hour12: false });
}

function renderChatMessages(messages = []) {
  if (!messages.length) {
    return `
      <div class="chat-empty">
        <h3>还没有历史对话</h3>
        <p>在下面输入一句话，用来测试当前人设、记忆和模型配置。</p>
      </div>
    `;
  }

  return messages.map(item => {
    const role = item.role === 'user' ? 'user' : 'assistant';
    const label = role === 'user' ? '你' : 'Hermiss';
    const rawContent = String(item.content || '');
    const content = chatEscapeHtml(rawContent).replace(/\n/g, '<br>');
    return `
      <article class="chat-message ${role}">
        <div class="chat-bubble">
          <div class="chat-meta">
            <span class="chat-speaker">${label}</span>
            <div class="chat-actions">
              <time>${chatEscapeHtml(chatFormatTime(item.timestamp))}</time>
              <button class="chat-copy" type="button" data-copy="${chatEscapeAttr(rawContent)}" aria-label="复制这条消息">复制</button>
            </div>
          </div>
          <div class="chat-text">${content}</div>
        </div>
      </article>
    `;
  }).join('');
}

async function loadChatHistory({ silent = false } = {}) {
  const list = document.getElementById('chat-history');
  if (!list) return;
  if (!silent) list.innerHTML = '<div class="empty">正在加载历史对话...</div>';
  const data = await api('/api/chat/history?limit=120');
  list.innerHTML = renderChatMessages(data.messages || []);
  list.scrollTop = list.scrollHeight;
}

window.Pages.chat = async function(el) {
  el.innerHTML = `
    <div class="page-head">
      <div>
        <h2>聊天窗口</h2>
        <p class="page-subtitle">直接和当前 Hermiss 容器对话，用于测试人设、记忆、表情包和模型配置。</p>
      </div>
      <button class="btn btn-sm" id="btn-chat-refresh">刷新历史</button>
    </div>

    <section class="chat-panel card" aria-label="聊天测试窗口">
      <div class="chat-history" id="chat-history" aria-live="polite"></div>
      <form class="chat-compose" id="chat-compose">
        <label class="sr-only" for="chat-input">输入消息</label>
        <textarea id="chat-input" rows="2" maxlength="4000" placeholder="输入一句话测试 Hermiss。Enter 发送，Shift + Enter 换行。"></textarea>
        <button class="btn btn-primary" id="btn-chat-send" type="submit">发送</button>
      </form>
      <p class="form-hint">这里会调用当前容器的 Hermes CLI，历史记录与容器内真实会话数据库一致。</p>
    </section>
  `;

  await loadChatHistory();

  const history = document.getElementById('chat-history');
  history?.addEventListener('click', async event => {
    const button = event.target.closest('.chat-copy');
    if (!button) return;
    const text = button.dataset.copy || '';
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
      button.textContent = '已复制';
      toast('已复制消息', 'ok');
      setTimeout(() => { button.textContent = '复制'; }, 1200);
    } catch (e) {
      toast('复制失败，请手动选择文本', 'err');
    }
  });

  document.getElementById('btn-chat-refresh')?.addEventListener('click', async () => {
    try {
      await loadChatHistory();
      toast('历史已刷新', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    }
  });

  const form = document.getElementById('chat-compose');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('btn-chat-send');

  input?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form?.requestSubmit();
    }
  });

  form?.addEventListener('submit', async e => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return toast('请输入消息', 'err');

    sendBtn.disabled = true;
    sendBtn.textContent = '回复中...';
    input.disabled = true;

    const list = document.getElementById('chat-history');
    if (list) {
      const now = Date.now() / 1000;
      const current = list.innerHTML === '<div class="empty">正在加载历史对话...</div>' ? '' : list.innerHTML;
      list.innerHTML = current + renderChatMessages([{ role: 'user', content: message, timestamp: now }]) +
        '<div class="chat-thinking">Hermiss 正在回复...</div>';
      list.scrollTop = list.scrollHeight;
    }

    try {
      const data = await api('/api/chat/send', {
        method: 'POST',
        body: JSON.stringify({ message }),
      });
      input.value = '';
      const messages = data.history?.messages || [];
      if (list) {
        list.innerHTML = renderChatMessages(messages);
        list.scrollTop = list.scrollHeight;
      }
    } catch (err) {
      toast(err.message, 'err');
      await loadChatHistory({ silent: true }).catch(() => {});
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = '发送';
      input.disabled = false;
      input.focus();
    }
  });
};
