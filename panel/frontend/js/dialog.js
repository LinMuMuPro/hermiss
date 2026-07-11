/* ── dialog.js ──
   Custom themed dialog system — replaces native prompt/confirm/alert.
   Promise-based API for clean async usage.
   Properly cleans up all event listeners to prevent memory leaks. */

"use strict";

let _dialogCleanup = null;

const $d = (sel) => document.querySelector(sel);

function _openDialog(msg, showInput, showCancel, showOk) {
  // Clean up previous dialog listeners
  if (_dialogCleanup) _dialogCleanup();

  const ov = $d('#dialog-overlay');
  $d('#dialog-msg').textContent = msg;
  $d('#dialog-input').style.display = showInput ? '' : 'none';
  $d('#dialog-cancel').style.display = showCancel ? '' : 'none';
  $d('#dialog-ok').style.display = showOk ? '' : 'none';
  ov.classList.add('open');

  return ov;
}

function _closeDialog() {
  $d('#dialog-overlay').classList.remove('open');
  if (_dialogCleanup) {
    _dialogCleanup();
    _dialogCleanup = null;
  }
}

function dialogConfirm(msg) {
  return new Promise(resolve => {
    const ov = _openDialog(msg, false, true, true);

    const handlers = {
      ok() { _closeDialog(); resolve(true); },
      cancel() { _closeDialog(); resolve(false); },
      overlay(e) { if (e.target === ov) { _closeDialog(); resolve(false); } },
      key(e) {
        if (e.key === 'Escape') { _closeDialog(); resolve(false); }
        if (e.key === 'Enter') { _closeDialog(); resolve(true); }
      }
    };

    $d('#dialog-ok').addEventListener('click', handlers.ok);
    $d('#dialog-cancel').addEventListener('click', handlers.cancel);
    ov.addEventListener('click', handlers.overlay);
    document.addEventListener('keydown', handlers.key);

    _dialogCleanup = () => {
      $d('#dialog-ok').removeEventListener('click', handlers.ok);
      $d('#dialog-cancel').removeEventListener('click', handlers.cancel);
      ov.removeEventListener('click', handlers.overlay);
      document.removeEventListener('keydown', handlers.key);
    };
  });
}

function dialogPrompt(msg, def = '') {
  return new Promise(resolve => {
    const ov = _openDialog(msg, true, true, true);
    const inp = $d('#dialog-input');
    inp.value = def;

    const handlers = {
      ok() { _closeDialog(); resolve(inp.value); },
      cancel() { _closeDialog(); resolve(null); },
      overlay(e) { if (e.target === ov) { _closeDialog(); resolve(null); } },
      key(e) { if (e.key === 'Escape') { _closeDialog(); resolve(null); } },
      inputKey(e) { if (e.key === 'Enter') $d('#dialog-ok').click(); }
    };

    $d('#dialog-ok').addEventListener('click', handlers.ok);
    $d('#dialog-cancel').addEventListener('click', handlers.cancel);
    ov.addEventListener('click', handlers.overlay);
    document.addEventListener('keydown', handlers.key);
    inp.addEventListener('keydown', handlers.inputKey);

    _dialogCleanup = () => {
      $d('#dialog-ok').removeEventListener('click', handlers.ok);
      $d('#dialog-cancel').removeEventListener('click', handlers.cancel);
      ov.removeEventListener('click', handlers.overlay);
      document.removeEventListener('keydown', handlers.key);
      inp.removeEventListener('keydown', handlers.inputKey);
    };

    setTimeout(() => inp.focus(), 50);
  });
}

function dialogAlert(msg) {
  return new Promise(resolve => {
    const ov = _openDialog(msg, false, false, true);

    const handlers = {
      ok() { _closeDialog(); resolve(); },
      key(e) { if (e.key === 'Enter' || e.key === 'Escape') { _closeDialog(); resolve(); } }
    };

    $d('#dialog-ok').addEventListener('click', handlers.ok);
    document.addEventListener('keydown', handlers.key);

    _dialogCleanup = () => {
      $d('#dialog-ok').removeEventListener('click', handlers.ok);
      document.removeEventListener('keydown', handlers.key);
    };
  });
}
