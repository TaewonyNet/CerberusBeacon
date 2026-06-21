// ─── i18n ────────────────────────────────────────────────────────────────────
const LANG = "%(lang)s";
const I18N = %(i18n_js)s;
// ─── 전역 상태 ───────────────────────────────────────────────────────────────
const TERMINAL_ENABLED = %(terminal_enabled_js)s;
const SERVER_PORT = %(server_port_js)s;
let activeSessions = {};   // session_id → {ws, term, fitAddon}
let activeSessionId = null;
let selectedDir = null;

// console.log 미러
const _consoleLogs = [];
const _origLog = console.log.bind(console);
console.log = (...args) => {
  _origLog(...args);
  _consoleLogs.push(args.map(a=>typeof a==='object'?JSON.stringify(a):String(a)).join(' '));
  if (_consoleLogs.length > 200) _consoleLogs.shift();
};
const _networkLogs = [];
function logNetwork(url, status, ms) {
  _networkLogs.unshift({url, status, ms, t: new Date().toLocaleTimeString()});
  if (_networkLogs.length > 100) _networkLogs.pop();
}

// ─── Toast ───────────────────────────────────────────────────────────────────
function showToast(msg, color='') {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  if (color) el.style.borderColor = color;
  document.getElementById('toast-container').appendChild(el);
  requestAnimationFrame(() => { el.classList.add('show'); });
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 4000);
}

// ─── API 헬퍼 ────────────────────────────────────────────────────────────────
async function apiFetch(method, path, body) {
  const t0 = performance.now();
  try {
    const opts = { method, headers: {'Content-Type':'application/json'} };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    const ms = Math.round(performance.now() - t0);
    logNetwork(path, r.status, ms);
    if (!r.ok) {
      const txt = await r.text();
      showToast(I18N.api_error + r.status + ': ' + txt, 'var(--red)');
      return null;
    }
    return await r.json();
  } catch(e) {
    showToast(I18N.network_error + e.message, 'var(--red)');
    logNetwork(path, 'ERR', 0);
    return null;
  }
}

// ─── 사이드바 토글 ────────────────────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}
function toggleAcc(id) {
  const body = document.getElementById(id);
  const arr = document.getElementById('arr-' + id);
  const open = body.classList.toggle('open');
  if (arr) arr.textContent = open ? '▼' : '▶';
}

// ─── 디버그 패널 ──────────────────────────────────────────────────────────────
let _debugTab = 'console';
function toggleDebug() {
  document.getElementById('debug-panel').classList.toggle('open');
  renderDebugTab();
}
function switchDebugTab(tab) {
  _debugTab = tab;
  document.querySelectorAll('.debug-tab').forEach(el => el.classList.remove('active'));
  event.target.classList.add('active');
  renderDebugTab();
}
async function renderDebugTab() {
  const el = document.getElementById('debug-content');
  if (_debugTab === 'console') {
    el.innerHTML = _consoleLogs.map(l=>`<div>${escHtml(l)}</div>`).join('');
  } else if (_debugTab === 'network') {
    el.innerHTML = _networkLogs.map(l=>`<div>[${l.t}] ${l.status} ${l.ms}ms ${escHtml(l.url)}</div>`).join('');
  } else {
    const [m, h] = await Promise.all([
      fetch('/api/metrics').then(r=>r.json()).catch(()=>({})),
      fetch('/health').then(r=>r.json()).catch(()=>({}))
    ]);
    el.textContent = 'Metrics: ' + JSON.stringify(m, null, 2) + '\n\nHealth: ' + JSON.stringify(h, null, 2);
  }
}
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ─── 터널 ────────────────────────────────────────────────────────────────────
async function loadTunnels() {
  const data = await apiFetch('GET', '/api/tunnel/status');
  if (!data) return;
  const el = document.getElementById('tunnel-list');
  if (!data.tunnels || data.tunnels.length === 0) {
    el.innerHTML = `<div style="color:var(--text-dim);font-size:0.8rem">${I18N.tunnel_none}</div>`;
    return;
  }
  el.innerHTML = data.tunnels.map(t => {
    const locked = !t.track_activity;
    const lockIcon = locked ? '🔒' : '⏱';
    const lockLabel = locked ? I18N.tunnel_lock_hint : `${I18N.tunnel_auto_hint}${t.idle_sec}${I18N.tunnel_auto_unit}`;
    const idleInfo = t.track_activity ? ` — ${Math.floor(t.idle_sec/60)}m${t.idle_sec%60}s` : I18N.tunnel_permanent;
    return `
    <div class="tunnel-item">
      <div style="display:flex;align-items:center;gap:0.3rem">
        <span>${I18N.tunnel_local}${t.port} → ${I18N.tunnel_ext}${idleInfo}</span>
      </div>
      <div class="tunnel-url"><a href="${escHtml(t.url)}" target="_blank" style="color:var(--accent)">${escHtml(t.url)}</a></div>
      <div class="tunnel-btns">
        <button onclick="copyText('${escHtml(t.url)}')" title="[[tunnel_url_copy]]">[[btn_copy]]</button>
        <button onclick="toggleTunnelLock(${t.port})" title="${lockLabel}">${lockIcon}</button>
        <button onclick="closeTunnel(${t.port})" title="[[tunnel_close_hint]]">[[btn_close]]</button>
      </div>
    </div>`;
  }).join('');
}
async function openTunnel() {
  const input = document.getElementById('tunnel-port-input');
  const portVal = input ? input.value.trim() : '';
  const port = portVal ? parseInt(portVal) : SERVER_PORT;
  if (!port || port < 1 || port > 65535) { showToast(I18N.toast_invalid_port || '포트 범위: 1-65535', 'var(--red)'); return; }
  const r = await apiFetch('POST', '/api/tunnel/open', {port});
  if (r) { showToast(I18N.toast_opened); loadTunnels(); }
}
async function closeTunnel(port) {
  const r = await apiFetch('POST', '/api/tunnel/close', {port});
  if (r) { showToast(I18N.toast_closed); loadTunnels(); }
}
async function toggleTunnelLock(port) {
  const r = await apiFetch('POST', '/api/tunnel/lock', {port});
  if (r) { showToast(I18N.toast_changed); loadTunnels(); }
}
function copyText(t) {
  navigator.clipboard.writeText(t).then(()=>showToast(I18N.toast_copied));
}

// ─── 메트릭 ──────────────────────────────────────────────────────────────────
function barHtml(pct, color='var(--accent)') {
  return `<div class="metric-bar"><div class="metric-bar-fill" style="width:${pct}%;background:${color}"></div></div>`;
}
async function loadMetrics() {
  const data = await apiFetch('GET', '/api/metrics');
  if (!data) {
    document.getElementById('metrics-content').innerHTML =
      '<div style="color:var(--text-dim)">— <button onclick="loadMetrics()" style="font-size:0.75rem;background:none;border:1px solid var(--border);border-radius:3px;color:var(--text);cursor:pointer">' + I18N.btn_retry + '</button></div>';
    return;
  }
  const cpuLabel = data.cpu_available === false ? 'N/A' : (data.cpu_pct === 0 ? I18N.cpu_measuring : data.cpu_pct.toFixed(1) + '%');
  document.getElementById('metrics-content').innerHTML = `
    <div class="metric-bar-wrap">
      <div class="metric-label"><span>CPU</span><span>${cpuLabel}</span></div>
      ${barHtml(data.cpu_pct)}
    </div>
    <div class="metric-bar-wrap">
      <div class="metric-label"><span>MEM</span><span>${data.mem_pct.toFixed(1)}%</span></div>
      ${barHtml(data.mem_pct,'#3fb950')}
    </div>
    <div class="metric-bar-wrap">
      <div class="metric-label"><span>DISK</span><span>${data.disk_pct.toFixed(1)}%</span></div>
      ${barHtml(data.disk_pct,'#d29922')}
    </div>
    <div style="font-size:0.75rem;color:var(--text-dim);margin-top:0.3rem">
      MEM ${data.mem_used_gb}/${data.mem_total_gb}GB  DISK ${data.disk_used_gb}/${data.disk_total_gb}GB
    </div>`;
}

// ─── 파일 트리 ───────────────────────────────────────────────────────────────
let _treeCache = {};
async function loadTree(path, container) {
  const url = '/api/tree?path=' + encodeURIComponent(path) + (%(hidden_flag)s ? '&hidden=1' : '');
  const data = await apiFetch('GET', url);
  if (!data) return;
  container.innerHTML = '';
  data.dirs.forEach(d => {
    const div = document.createElement('div');
    div.className = 'file-node dir';
    div.textContent = d.name;
    div.title = d.path;
    const children = document.createElement('div');
    children.style.paddingLeft = '0.8rem';
    children.style.display = 'none';
    let loaded = false;
    div.onclick = async (e) => {
      e.stopPropagation();
      selectDir(d.path, div);
      if (children.style.display === 'none') {
        children.style.display = 'block';
        if (!loaded) { loaded = true; await loadTree(d.path, children); }
      } else {
        children.style.display = 'none';
      }
    };
    container.appendChild(div);
    container.appendChild(children);
  });
  data.files.forEach(f => {
    const div = document.createElement('div');
    div.className = 'file-node file';
    div.textContent = f.name + ' (' + fmtSize(f.size) + ')';
    div.title = f.path;
    div.onclick = (e) => { e.stopPropagation(); downloadFile(f.path); };
    container.appendChild(div);
  });
}
function fmtSize(b) {
  if (b < 1024) return b + 'B';
  if (b < 1048576) return (b/1024).toFixed(1) + 'KB';
  if (b < 1073741824) return (b/1048576).toFixed(1) + 'MB';
  return (b/1073741824).toFixed(1) + 'GB';
}
function selectDir(path, el) {
  document.querySelectorAll('.file-node.selected').forEach(n=>n.classList.remove('selected'));
  el.classList.add('selected');
  selectedDir = path;
  document.getElementById('upload-target').textContent = I18N.upload_target + path;
  document.getElementById('upload-btn').disabled = false;
  document.getElementById('upload-btn').title = '';
}
function downloadFile(path) {
  location.href = '/api/download?path=' + encodeURIComponent(path);
}
function triggerUpload() {
  if (!selectedDir) { showToast(I18N.toast_select_dir); return; }
  document.getElementById('file-input').click();
}
async function doUpload(e) {
  const files = e.target.files;
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append('file', f);
  const t0 = performance.now();
  try {
    const r = await fetch('/api/upload?path=' + encodeURIComponent(selectedDir), {method:'POST', body:fd});
    logNetwork('/api/upload', r.status, Math.round(performance.now()-t0));
    if (r.ok) {
      showToast(I18N.toast_upload_done);
      loadTree(%(files_root_js)s, document.getElementById('file-tree'));
    } else {
      showToast(I18N.toast_upload_fail + r.status, 'var(--red)');
    }
  } catch(e) { showToast(I18N.toast_upload_err + e.message, 'var(--red)'); }
  e.target.value = '';
}
// 드래그앤드롭
document.addEventListener('DOMContentLoaded', () => {
  const tree = document.getElementById('file-tree');
  ['dragenter','dragover'].forEach(ev => {
    tree.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); });
  });
  tree.addEventListener('drop', async e => {
    e.preventDefault(); e.stopPropagation();
    if (!selectedDir) { showToast(I18N.toast_select_dir2); return; }
    const fd = new FormData();
    for (const f of e.dataTransfer.files) fd.append('file', f);
    const r = await fetch('/api/upload?path='+encodeURIComponent(selectedDir),{method:'POST',body:fd});
    if (r.ok) showToast(I18N.toast_drop_done);
    else showToast(I18N.toast_drop_fail, 'var(--red)');
  });
});

// ─── 하단 바 (특수키 + 매크로) ────────────────────────────────────────────
let ctrlActive = false, altActive = false, shiftActive = false;
// 모바일 가상 키보드: keydown.preventDefault()가 input 이벤트를 못 막는 iOS 우회
let _suppressXtermInput = false;

function _updateModKeyRow() {
  const row = document.getElementById('mod-key-row');
  if (!row) return;
  const anyActive = ctrlActive || altActive || shiftActive;
  if (!anyActive) { row.classList.remove('active'); row.innerHTML = ''; return; }
  // CTRL/ALT 활성 시 a-z 알파벳 버튼 표시 (모바일 조합 입력용)
  // SHIFT만 활성 시 숫자/특수문자 행 표시
  row.innerHTML = '';
  if (ctrlActive || altActive) {
    'abcdefghijklmnopqrstuvwxyz'.split('').forEach(ch => {
      const b = document.createElement('button');
      b.className = 'mk'; b.textContent = ch.toUpperCase();
      b.title = (ctrlActive ? 'Ctrl' : 'Alt') + '+' + ch.toUpperCase();
      b.onclick = () => sendSpecial(ch);
      row.appendChild(b);
    });
    // 숫자 0-9도 추가
    '1234567890'.split('').forEach(ch => {
      const b = document.createElement('button');
      b.className = 'mk'; b.textContent = ch;
      b.title = (ctrlActive ? 'Ctrl' : 'Alt') + '+' + ch;
      b.onclick = () => sendSpecial(ch);
      row.appendChild(b);
    });
  } else if (shiftActive) {
    // SHIFT만: 특수문자 행 (숫자 키의 shift 변환)
    '!@#$%^&*()_+{}|:"<>?'.split('').forEach(ch => {
      const b = document.createElement('button');
      b.className = 'mk'; b.textContent = ch;
      b.onclick = () => sendSpecial(ch);
      row.appendChild(b);
    });
  }
  row.classList.add('active');
}

function toggleCtrl() {
  ctrlActive = !ctrlActive;
  if (ctrlActive && altActive) { altActive = false; document.getElementById('btn-alt').classList.remove('active'); }
  document.getElementById('btn-ctrl').classList.toggle('active', ctrlActive);
  _updateModKeyRow();
}
function toggleAlt() {
  altActive = !altActive;
  if (altActive && ctrlActive) { ctrlActive = false; document.getElementById('btn-ctrl').classList.remove('active'); }
  document.getElementById('btn-alt').classList.toggle('active', altActive);
  _updateModKeyRow();
}
function toggleShift() {
  shiftActive = !shiftActive;
  document.getElementById('btn-shift').classList.toggle('active', shiftActive);
  _updateModKeyRow();
}

function _applyMods(seq, forceCtrl, forceAlt, forceShift) {
  const _ca = {'\x1b[A':'\x1b[1;5A','\x1b[B':'\x1b[1;5B','\x1b[C':'\x1b[1;5C','\x1b[D':'\x1b[1;5D','\x1b[H':'\x1b[1;5H','\x1b[F':'\x1b[1;5F'};
  const _sa = {'\x1b[A':'\x1b[1;2A','\x1b[B':'\x1b[1;2B','\x1b[C':'\x1b[1;2C','\x1b[D':'\x1b[1;2D',
    '\x1b[H':'\x1b[1;2H','\x1b[F':'\x1b[1;2F','\x1b[5~':'\x1b[5;2~','\x1b[6~':'\x1b[6;2~',
    '\t':'\x1b[Z','\x1bOP':'\x1b[1;2P','\x1bOQ':'\x1b[1;2Q','\x1bOR':'\x1b[1;2R','\x1bOS':'\x1b[1;2S',
    '\x1b[15~':'\x1b[15;2~','\x1b[17~':'\x1b[17;2~','\x1b[18~':'\x1b[18;2~','\x1b[19~':'\x1b[19;2~',
    '\x1b[20~':'\x1b[20;2~','\x1b[21~':'\x1b[21;2~','\x1b[23~':'\x1b[23;2~','\x1b[24~':'\x1b[24;2~'};
  let s = seq;
  let _changed = false;
  if (ctrlActive || forceCtrl) {
    s = _ca[s] || (s.length===1 && s.charCodeAt(0)>=0x20 ? String.fromCharCode(s.charCodeAt(0)&0x1f) : s);
    if (ctrlActive) { ctrlActive = false; document.getElementById('btn-ctrl').classList.remove('active'); _changed=true; }
  }
  if (shiftActive || forceShift) {
    s = _sa[s] || (s.length===1 ? s.toUpperCase() : s);
    if (shiftActive) { shiftActive = false; document.getElementById('btn-shift').classList.remove('active'); _changed=true; }
  }
  if (altActive || forceAlt) {
    s = '\x1b' + s;
    if (altActive) { altActive = false; document.getElementById('btn-alt').classList.remove('active'); _changed=true; }
  }
  if (_changed) _updateModKeyRow();
  return s;
}

function sendSpecial(seq) {
  if (!activeSessionId || !activeSessions[activeSessionId]) return;
  const ws = activeSessions[activeSessionId].ws;
  if (ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify(['stdin', _applyMods(seq)]));
}

// 물리 키보드 Ctrl/Alt/Shift + toggle 변조 통합 처리
document.addEventListener('keydown', function(e) {
  if (document.activeElement?.closest('.cb-modal')) return;
  const termFocused = !!document.activeElement?.closest('#terminal-container');
  const ws = activeSessions[activeSessionId]?.ws;
  // 물리 Shift+Tab → back-tab (xterm이 못 잡는 경우 대비)
  if (e.shiftKey && !e.ctrlKey && !e.altKey && e.key==='Tab' && termFocused) {
    if (ws && ws.readyState===WebSocket.OPEN) {
      e.preventDefault(); e.stopPropagation();
      ws.send(JSON.stringify(['stdin','\x1b[Z']));
    }
    return;
  }
  const physCtrl = e.ctrlKey && !e.metaKey && termFocused;
  const physAlt = e.altKey && termFocused;
  if (!ctrlActive && !altActive && !shiftActive && !physCtrl && !physAlt) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    ctrlActive = false; altActive = false; shiftActive = false;
    ['btn-ctrl','btn-alt','btn-shift'].forEach(id=>document.getElementById(id).classList.remove('active'));
    return;
  }
  if (['Control','Alt','Shift','Meta'].includes(e.key)) return;
  const _km = {ArrowUp:'\x1b[A',ArrowDown:'\x1b[B',ArrowLeft:'\x1b[D',ArrowRight:'\x1b[C',
    Home:'\x1b[H',End:'\x1b[F',PageUp:'\x1b[5~',PageDown:'\x1b[6~',
    Tab:'\t',Escape:'\x1b',Enter:'\r',Backspace:'\x7f',Delete:'\x1b[3~',
    F1:'\x1bOP',F2:'\x1bOQ',F3:'\x1bOR',F4:'\x1bOS',
    F5:'\x1b[15~',F6:'\x1b[17~',F7:'\x1b[18~',F8:'\x1b[19~',
    F9:'\x1b[20~',F10:'\x1b[21~',F11:'\x1b[23~',F12:'\x1b[24~'};
  const seq = e.key.length===1 ? e.key : (_km[e.key]||'');
  if (!seq) return;
  const _wasToggle = ctrlActive || altActive || shiftActive;
  e.preventDefault(); e.stopPropagation();
  ws.send(JSON.stringify(['stdin', _applyMods(seq, physCtrl, physAlt, false)]));
  // 모바일: toggle modifier 사용 후 iOS가 input 이벤트로 원문자를 한 번 더 보내는 것 차단
  if (_wasToggle) {
    _suppressXtermInput = true;
    setTimeout(() => { _suppressXtermInput = false; }, 150);
  }
}, true);

// JS 오류 → 디버그 패널 열기 + 콘솔에 기록
let _dbgAutoCloseTimer = null;
function _dbgOpenOnError() {
  const p = document.getElementById('debug-panel');
  if (p) { p.classList.add('open'); renderDebugTab(); }
  if (_dbgAutoCloseTimer) { clearTimeout(_dbgAutoCloseTimer); _dbgAutoCloseTimer = null; }
}
window.addEventListener('error', function(e) {
  _consoleLogs.push('[ERROR] ' + e.message + ' @ ' + (e.filename ? e.filename.split('/').pop() : '?') + ':' + e.lineno);
  _dbgOpenOnError();
});
window.addEventListener('unhandledrejection', function(e) {
  _consoleLogs.push('[PROMISE] ' + String(e.reason));
  _dbgOpenOnError();
});

// visualViewport로 키보드 위에 하단 바 고정 (iOS Safari 대응)
(function() {
  const bar = document.getElementById('bottom-bar');
  if (!bar || !window.visualViewport) return;
  function update() {
    const vv = window.visualViewport;
    const offset = window.innerHeight - vv.height - vv.offsetTop;
    bar.style.bottom = Math.max(0, offset) + 'px';
  }
  window.visualViewport.addEventListener('resize', update);
  window.visualViewport.addEventListener('scroll', update);
})();

let _macros = [];
async function loadMacros() {
  const data = await apiFetch('GET', '/api/macros');
  if (data) { _macros = data; renderMacroBar(); }
}
function renderMacroBar() {
  const bar = document.getElementById('macro-bar');
  if (!bar) return;
  bar.innerHTML = '';
  _macros.forEach((m, i) => {
    if (!m.send) return;
    const btn = document.createElement('button');
    btn.className = 'mb'; btn.textContent = m.label;
    btn.title = m.send.replace(/\n/g,'↵');
    btn.onclick = () => sendMacro(m.send);
    btn.oncontextmenu = e => { e.preventDefault(); openMacroEdit(i); };
    bar.appendChild(btn);
  });
  const editBtn = document.createElement('button');
  editBtn.className = 'mb mb-edit'; editBtn.textContent = '✏️';
  editBtn.title = I18N.macro_edit_add; editBtn.onclick = () => openMacroEdit(-1);
  bar.appendChild(editBtn);
  if (TERMINAL_ENABLED) document.getElementById('bottom-bar').classList.add('visible');
}
function sendMacro(text) {
  if (!activeSessionId || !activeSessions[activeSessionId]) { showToast(I18N.toast_no_session); return; }
  const ws = activeSessions[activeSessionId].ws;
  if (ws.readyState !== WebSocket.OPEN) { showToast(I18N.toast_connecting); return; }
  ws.send(JSON.stringify(['stdin', text]));
}

// 매크로 편집 모달
let _macroEditIdx = -1;
function openMacroEdit(idx) {
  _macroEditIdx = idx;
  const isNew = idx === -1;
  document.getElementById('macro-modal-title').textContent = isNew ? '[[macro_add]]' : '[[macro_edit]]';
  document.getElementById('mm-del').style.display = isNew ? 'none' : '';
  document.getElementById('mm-label').value = isNew ? '' : _macros[idx].label;
  document.getElementById('mm-send').value = isNew ? '' : (_macros[idx].send||'')
    .replace(/\n/g,'\\n').replace(/\r/g,'\\r').replace(/\x03/g,'\\x03').replace(/\x04/g,'\\x04');
  document.getElementById('macro-modal').classList.add('open');
  setTimeout(()=>document.getElementById('mm-label').focus(), 50);
}
function closeMacroModal() { document.getElementById('macro-modal').classList.remove('open'); }
async function saveMacroEdit() {
  const label = document.getElementById('mm-label').value.trim();
  const raw   = document.getElementById('mm-send').value;
  if (!label) { showToast(I18N.toast_enter_name); return; }
  const send = raw.replace(/\\n/g,'\n').replace(/\\r/g,'\r').replace(/\\t/g,'\t')
                  .replace(/\\x03/gi,'\x03').replace(/\\x04/gi,'\x04');
  const list = [..._macros];
  if (_macroEditIdx === -1) list.push({label, send});
  else list[_macroEditIdx] = {label, send};
  await _saveMacros(list);
  closeMacroModal();
}
async function deleteMacroEdit() {
  if (_macroEditIdx < 0) return;
  if (!confirm(I18N.confirm_delete_macro || '매크로를 삭제하시겠습니까?')) return;
  await _saveMacros(_macros.filter((_,i)=>i!==_macroEditIdx));
  closeMacroModal();
}
async function _saveMacros(list) {
  const r = await apiFetch('POST','/api/macros',list);
  if (r && r.ok) { _macros = list; renderMacroBar(); showToast(I18N.toast_saved); }
  else showToast(I18N.toast_save_fail);
}

// ─── 설정 ────────────────────────────────────────────────────────────────────
async function openSettings() {
  const r = await apiFetch('GET','/api/settings');
  if (r) {
    document.getElementById('st-tg-token').value = r.tg_token || '';
    document.getElementById('st-tg-chat').value  = r.tg_chat_id || '';
    document.getElementById('st-idle').value     = r.idle_timeout ?? 5;
    document.getElementById('st-shell').value    = r.terminal_shell || '/bin/bash';
    const langSel = document.getElementById('st-lang');
    if (langSel) langSel.value = r.lang ?? '';
  }
  document.getElementById('settings-modal').classList.add('open');
}
function closeSettings() { document.getElementById('settings-modal').classList.remove('open'); }
async function saveSettings() {
  const langSel = document.getElementById('st-lang');
  const r = await apiFetch('POST','/api/settings',{
    tg_token:      document.getElementById('st-tg-token').value.trim(),
    tg_chat_id:    parseInt(document.getElementById('st-tg-chat').value)||0,
    idle_timeout:  parseInt(document.getElementById('st-idle').value)||0,
    terminal_shell:document.getElementById('st-shell').value.trim()||'/bin/bash',
    lang:          langSel ? langSel.value : LANG,
  });
  if (r && r.ok) { showToast(I18N.toast_settings_saved); closeSettings(); if (langSel && langSel.value !== LANG) location.reload(); }
  else showToast(I18N.toast_save_fail);
}

// ─── 터미널 ──────────────────────────────────────────────────────────────────
%(terminal_js)s

// ─── 클라이언트 유휴 자동 로그아웃 (5분) ─────────────────────────────────────
let _clientIdleTimer = null;
function _resetClientIdle() {
  if (_clientIdleTimer) clearTimeout(_clientIdleTimer);
  _clientIdleTimer = setTimeout(() => { location.href = '/logout'; }, 5 * 60 * 1000);
}
document.addEventListener('keydown', _resetClientIdle, true);
document.addEventListener('mousedown', _resetClientIdle, { passive: true });
document.addEventListener('touchstart', _resetClientIdle, { passive: true });
_resetClientIdle();

// ─── 초기화 ──────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  // 디버그 패널: HTML에서 이미 열린 상태 → 정상 로드 시 3초 후 자동 닫기
  renderDebugTab();
  _dbgAutoCloseTimer = setTimeout(() => {
    const _dbgPanel = document.getElementById('debug-panel');
    if (_dbgPanel) _dbgPanel.classList.remove('open');
    _dbgAutoCloseTimer = null;
  }, 3000);

  const _portInput = document.getElementById('tunnel-port-input');
  if (_portInput && !_portInput.value) _portInput.value = String(SERVER_PORT);
  loadTunnels();
  setInterval(loadTunnels, 10000);
  loadMetrics();
  setInterval(loadMetrics, 5000);
  loadTree(%(files_root_js)s, document.getElementById('file-tree'));
  if (TERMINAL_ENABLED) {
    loadMacros();
    await loadSessions();
  }
  // 초기 아코디언 화살표 상태
  ['acc-tunnel','acc-metrics','acc-files','acc-session-list'].forEach(id => {
    const arr = document.getElementById('arr-' + id);
    if (arr) arr.textContent = '▼';
  });
});
