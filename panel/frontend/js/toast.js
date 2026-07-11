/* ── toast.js ──
   Toast notification system with close button and aria-live */

function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'toast' + (type ? ' toast-' + type : '');
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');

  const text = document.createElement('span');
  text.textContent = msg;
  el.appendChild(text);

  const close = document.createElement('button');
  close.className = 'toast-close';
  close.innerHTML = '&times;';
  close.setAttribute('aria-label', '关闭');
  close.addEventListener('click', () => dismiss(el));
  el.appendChild(close);

  document.getElementById('toast-container').appendChild(el);

  const timer = setTimeout(() => dismiss(el), 4000);

  function dismiss(target) {
    clearTimeout(timer);
    target.style.opacity = '0';
    target.style.transform = 'translateX(20px)';
    setTimeout(() => target.remove(), 200);
  }
}
