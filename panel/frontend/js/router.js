window.Pages = window.Pages || {};
const routes = [
  'persona', 'memory', 'stickers', 'chat', 'cron', 'settings'
];

let currentRoute = 'persona';
let _navigating = false;

function navigate(route) {
  if (_navigating) return;
  const base = route.split('/')[0];
  if (!routes.includes(base)) return;
  if (currentRoute === route) { _renderRoute(route); return; }
  _navigating = true;
  currentRoute = route;
  location.hash = route;
  _renderRoute(route);
  _navigating = false;
}

function _renderRoute(route) {
  const base = route.split('/')[0];
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const pageEl = document.getElementById(`page-${base}`);
  if (pageEl) pageEl.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const navItem = document.querySelector(`.nav-item[data-route="${base}"]`) || document.querySelector(`.nav-item[data-route="${route}"]`);
  if (navItem) navItem.classList.add('active');
  const el = document.getElementById(`page-${base}`);
  if (!el) return;
  el.innerHTML = '<div class="empty">加载中...</div>';
  const renderFn = window.Pages && window.Pages[base];
  if (renderFn) {
    renderFn(el, route).catch(err => {
      el.innerHTML = `<div class="empty">加载失败：${err.message}</div>`;
    });
  }
}

window.addEventListener('hashchange', () => {
  if (_navigating) return;
  const route = location.hash.slice(1) || 'persona';
  const base = route.split('/')[0];
  if (!routes.includes(base)) return navigate('persona');
  if (currentRoute === route) return;
  currentRoute = route;
  _renderRoute(route);
});

function initRoute() {
  const route = location.hash.slice(1);
  navigate(routes.includes(route.split('/')[0]) ? route : 'persona');
}
