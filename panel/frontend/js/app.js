/* ── app.js ──
   App init: auth check, sidebar, theme toggle, logout
   v4.0 — fixed sidebar (5 user / 8 admin), no simple mode */

const Q = (sel, parent = document) => parent.querySelector(sel);

// ── Theme ──
function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  const toggle = Q('#theme-toggle-input');
  if (toggle) toggle.checked = saved === 'dark';
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  const next = current === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem(THEME_KEY, next);
}

// ── User info ──
function getUser() {
  try {
    return JSON.parse(localStorage.getItem('hermes_user') || '{}');
  } catch (_) { return {}; }
}

function isAdmin() {
  return false;
}

// ── Sidebar ──
const userNavItems = [
  { route: 'persona', label: '人设', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6" cy="5" r="2"/><path d="M2 14c0-2.2 1.8-4 4-4 2.2 0 4 1.8 4 4"/><circle cx="10" cy="4" r="1.5"/><path d="M12 7.5c1.1 0 2 .9 2 2v2"/></svg>' },
  { route: 'memory', label: '记忆', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="1.5"/><path d="M5 7h6M5 10h4"/></svg>' },
  { route: 'stickers', label: '表情包', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M5.5 6.5h.01M10.5 6.5h.01M5.5 9.5c1.2 1.3 3.8 1.3 5 0" stroke-linecap="round"/></svg>' },
  { route: 'settings', label: '设置', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="3"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.1 3.1l1 1M11.9 11.9l1 1M3.1 12.9l1-1M11.9 4.1l1-1"/></svg>' },
];

const adminNavItems = [];

function initSidebar() {
  const nav = Q('#sidebar-nav');
  const footer = Q('#sidebar-footer');

  const items = userNavItems;

  nav.innerHTML = items.map(n =>
    `<button class="nav-item" data-route="${n.route}">${n.icon}<span>${n.label}</span></button>`
  ).join('');

  nav.addEventListener('click', e => {
    const item = e.target.closest('.nav-item');
    if (item) navigate(item.dataset.route);
  });

  footer.innerHTML = `
    <div class="theme-toggle">
      <span>暗色主题</span>
      <label class="theme-switch">
        <input type="checkbox" id="theme-toggle-input">
        <span class="theme-slider"></span>
      </label>
    </div>
    <button class="btn-logout" id="btn-logout">退出登录</button>
  `;

  Q('#theme-toggle-input').addEventListener('change', toggleTheme);
  initTheme();

  Q('#btn-logout').addEventListener('click', async () => {
    const ok = await dialogConfirm('确定退出登录？');
    if (ok) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem('hermes_user');
      showAuth();
    }
  });
}

// ── 全局：重启等待 ──
function showRestartOverlay(msg) {
  const ov = document.getElementById('restart-overlay');
  const m = document.getElementById('restart-msg');
  if (m) m.textContent = msg || '容器重启中，请稍候...';
  if (ov) ov.classList.add('open');
}

function hideRestartOverlay() {
  const ov = document.getElementById('restart-overlay');
  if (ov) ov.classList.remove('open');
}

async function waitForRestart(timeoutSec = 60) {
  const maxAttempts = Math.ceil(timeoutSec / 2);
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const s = await api('/api/container/status');
      if (s.status === 'running') return;
    } catch (_) { /* 继续等待 */ }
  }
  throw new Error(`容器启动超时（${timeoutSec}秒），请手动刷新`);
}

// ── Auth ──
let authMode = 'login';

function initAuth() {
  authMode = 'login';
  const title = Q('#auth-title');
  const sub = Q('#auth-sub');
  const btn = Q('#auth-submit');
  const toggle = Q('#auth-toggle');

  if (title) title.textContent = '登录';
  if (sub) sub.textContent = '单用户版本，默认账号 hermiss / hermiss';
  if (btn) btn.textContent = '登录';
  if (toggle && toggle.parentElement) toggle.parentElement.style.display = 'none';

  btn.addEventListener('click', async () => {
    const email = Q('#auth-email').value.trim();
    const password = Q('#auth-password').value;
    if (!email || !password) return toast('请填写账号和密码', 'err');

    btn.disabled = true;
    btn.textContent = '...';

    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
      });

      if (data.pending) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem('hermes_user');
        toast(data.message || '账号暂不可用', 'err');
        return;
      }

      if (data.access_token) {
        localStorage.setItem(TOKEN_KEY, data.access_token);
        localStorage.setItem('hermes_user', JSON.stringify({
          email: data.email || email,
          user_id: data.user_id,
          is_admin: false
        }));
        showApp();
        return;
      }

      toast('登录失败：服务器返回异常', 'err');
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '登录';
    }
  });

  Q('#auth-password').addEventListener('keydown', e => {
    if (e.key === 'Enter') btn.click();
  });
}

function showApp() {
  document.getElementById('auth-page').style.display = 'none';
  document.getElementById('app').style.display = 'block';
  initSidebar();
  initMobileMenu();

  const route = location.hash.slice(1);
  navigate(routes.includes(route.split("/")[0]) ? route : "persona");
}

// ── Mobile menu ──
function initMobileMenu() {
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  const btn = document.getElementById('mobile-menu-btn');

  if (!btn || !sidebar || !overlay) return;

  btn.addEventListener('click', () => {
    sidebar.classList.toggle('mobile-open');
    overlay.classList.toggle('open');
  });

  overlay.addEventListener('click', () => {
    sidebar.classList.remove('mobile-open');
    overlay.classList.remove('open');
  });

  // Close on nav click
  sidebar.addEventListener('click', e => {
    if (e.target.closest('.nav-item')) {
      sidebar.classList.remove('mobile-open');
      overlay.classList.remove('open');
    }
  });
}

function showAuth() {
  document.getElementById('app').style.display = 'none';
  document.getElementById('auth-page').style.display = '';
  Q('#auth-email').value = 'hermiss';
  Q('#auth-password').value = '';
  Q('#auth-form').style.display = '';
  const pending = Q('#auth-pending');
  if (pending) pending.style.display = 'none';
  const authToggle = Q('#auth-toggle');
  if (authToggle && authToggle.parentElement) authToggle.parentElement.style.display = 'none';
}

// ── Bootstrap ──
document.addEventListener('DOMContentLoaded', () => {
  initAuth();

  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    showAuth();
    return;
  }

  api('/api/auth/me')
    .then(() => showApp())
    .catch(err => {
      showAuth();
    });
});
