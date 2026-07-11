/* ── api.js ──
   Fetch wrapper with auth header, auto JSON parse, error handling,
   global 401 interception, and connection-loss detection. */

let _bouncing = false;
let _failCount = 0;
let _offlineToastShown = false;

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...opts.headers };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(API_BASE + path, { ...opts, headers });
  } catch (e) {
    // Network error — server unreachable
    _failCount++;
    if (_failCount >= 3 && !_offlineToastShown) {
      _offlineToastShown = true;
      if (typeof toast === 'function') {
        toast('连接已断开，请检查服务器', 'err');
      }
    }
    throw new Error('Failed to fetch');
  }

  // Connection restored
  if (_failCount > 0) {
    if (_offlineToastShown && typeof toast === 'function') {
      toast('连接已恢复', 'ok');
    }
    _failCount = 0;
    _offlineToastShown = false;
  }

  const data = await res.json().catch(() => ({}));

  // ── Global 401 handler ──
  const isAuthEndpoint = path.startsWith('/api/auth/');
  const onAuthPage = document.getElementById('auth-page') &&
                     document.getElementById('auth-page').style.display !== 'none';

  if (res.status === 401 && !isAuthEndpoint && !onAuthPage) {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem('hermes_user');

    const appEl = document.getElementById('app');
    if (appEl) appEl.style.display = 'none';

    const authEl = document.getElementById('auth-page');
    if (authEl) {
      authEl.style.display = 'flex';
      const emailInput = document.getElementById('auth-email');
      if (emailInput) emailInput.value = '';
      const pwInput = document.getElementById('auth-password');
      if (pwInput) pwInput.value = '';
      const form = document.getElementById('auth-form');
      if (form) form.style.display = '';
      const pending = document.getElementById('auth-pending');
      if (pending) pending.style.display = 'none';
    }

    throw new Error('登录已过期，请重新登录');
  }

  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}
