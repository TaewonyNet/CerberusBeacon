async function loadSessions() {
  const data = await apiFetch('GET', '/api/agent/sessions');
  if (!data) return;
  const sessions = data.sessions || [];
  const container = document.getElementById('session-list');
  if (container) {
    container.innerHTML = sessions.map(s =>
      `<div style="font-size:0.8rem;padding:0.2rem;cursor:pointer" onclick="openSession('${s}')">${s}</div>`
    ).join('') || `<div style="color:var(--text-dim);font-size:0.8rem">${I18N.session_none}</div>`;
  }
  if (sessions.length > 0 && !activeSessionId) {
    openSession(sessions[0]);
  } else if (sessions.length === 0 && !activeSessionId) {
    const r = await apiFetch('POST', '/api/agent/session');
    if (r && r.session_id) {
      openSession(r.session_id);
      const container = document.getElementById('session-list');
      if (container) {
        container.innerHTML = `<div style="font-size:0.8rem;padding:0.2rem;cursor:pointer" onclick="openSession('${r.session_id}')">${r.session_id}</div>`;
      }
    }
  }
}
async function newTermTab() {
  const data = await apiFetch('POST', '/api/agent/session');
  if (!data) return;
  const sid = data.session_id;
  openSession(sid);
  loadSessions();
}
function openSession(sid) {
  if (activeSessions[sid]) {
    setActiveSession(sid); return;
  }
  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = wsProto + '//' + location.host + '/ws/' + sid;
  const ws = new WebSocket(wsUrl);
  let term = null;
  // 세션별 독립 pane 생성 — 탭 전환 시 show/hide
  const termContainer = document.getElementById('terminal');
  const pane = document.createElement('div');
  pane.id = 'term-pane-' + sid;
  pane.style.cssText = 'display:none;width:100%;height:100%;position:absolute;top:0;left:0';
  termContainer.appendChild(pane);
  if (typeof Terminal !== 'undefined') {
    term = new Terminal({theme:{background:'#0d1117',foreground:'#c9d1d9'},
                         fontFamily:'Menlo,Monaco,monospace',fontSize:14,cursorBlink:true});
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(pane);  // 공유 #terminal 대신 세션 전용 pane에 마운트
    fitAddon.fit();
    activeSessions[sid] = {ws, term, fitAddon, pane};
    // 모바일 modifier 조합 처리:
    // - Android Chrome: keydown이 'Unidentified'(IME)로 와서 우리 keydown 리스너가 못 잡음
    //   → input 이벤트에서 직접 modifier 적용
    // - iOS: keydown은 잡히지만 input도 별도 발화 → _suppressXtermInput으로 중복 제거
    const _xtermTA = pane.querySelector('textarea');
    if (_xtermTA) {
      _xtermTA.addEventListener('input', function(ev) {
        if (_suppressXtermInput) {
          // iOS/데스크탑 경로: keydown에서 이미 보냄, 중복 제거
          _suppressXtermInput = false;
          ev.stopImmediatePropagation();
          _xtermTA.value = '';
          return;
        }
        if (ctrlActive || altActive || shiftActive) {
          // Android Chrome IME 경로: input에서 실제 문자 취득 후 modifier 적용
          const char = (ev.data !== null && ev.data !== undefined) ? ev.data : _xtermTA.value;
          ev.stopImmediatePropagation();
          _xtermTA.value = '';
          const _ws = activeSessions[activeSessionId]?.ws;
          if (_ws && _ws.readyState === WebSocket.OPEN && char) {
            for (const c of char) _ws.send(JSON.stringify(['stdin', _applyMods(c)]));
          }
        }
      }, true);
    }
    term.onData(data => ws.send(JSON.stringify(['stdin', data])));
    // Tab → PTY, F1-F12 → 브라우저 기본 동작 차단 후 xterm 처리
    term.attachCustomKeyEventHandler(e => {
      if (e.type === 'keydown' && e.key === 'Tab') {
        ws.send(JSON.stringify(['stdin', '	']));
        e.preventDefault();
        return false;
      }
      if (e.type === 'keydown' && e.keyCode >= 112 && e.keyCode <= 123) {
        e.preventDefault();  // F5 새로고침, F12 개발자도구 등 브라우저 캡처 차단
        return true;
      }
      return true;
    });
    window.addEventListener('resize', () => {
      if (activeSessionId === sid) {
        fitAddon.fit();
        ws.send(JSON.stringify(['set_size', term.rows, term.cols]));
      }
    });
  } else {
    activeSessions[sid] = {ws, term: null, fitAddon: null, pane};
  }
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg[0] === 'stdout' && term) term.write(msg[1]);
  };
  ws.onclose = () => {
    const s = activeSessions[sid];
    if (s && s._intentionalClose) {
      if (s.pane) s.pane.remove();
      delete activeSessions[sid];
      removeTab(sid);
      const remaining = Object.keys(activeSessions);
      if (remaining.length > 0) setActiveSession(remaining[remaining.length - 1]);
      else { activeSessionId = null; _offerCloseTunnels(); }
    } else {
      _reconnectWs(sid, 1);
    }
  };
  ws.onopen = () => {
    const s = activeSessions[sid];
    if (s && s.term && s.fitAddon) {
      ws.send(JSON.stringify(['set_size', s.term.rows, s.term.cols]));
    }
  };
  addTab(sid);
  setActiveSession(sid);
}
function addTab(sid) {
  const tabs = document.getElementById('term-tabs');
  if (!tabs) return;
  if (document.getElementById('tab-' + sid)) return;
  const btn = document.createElement('button');
  btn.className = 'term-tab'; btn.id = 'tab-' + sid;
  btn.onclick = () => setActiveSession(sid);
  const label = document.createElement('span');
  label.textContent = sid;
  const closeX = document.createElement('span');
  closeX.textContent = ' ×';
  closeX.style.cssText = 'margin-left:5px;opacity:0.6;font-weight:bold;line-height:1';
  closeX.onclick = e => { e.stopPropagation(); closeTab(sid); };
  btn.appendChild(label);
  btn.appendChild(closeX);
  tabs.appendChild(btn);
}
function _reconnectWs(sid, attempt) {
  const session = activeSessions[sid];
  if (!session || session._intentionalClose) return;
  const maxAttempts = 5;
  if (attempt > maxAttempts) {
    if (session.term) session.term.write('\r\n\x1b[31m' + I18N.term_reconnect_failed + '\x1b[0m\r\n');
    if (session.pane) session.pane.remove();
    delete activeSessions[sid];
    removeTab(sid);
    const remaining = Object.keys(activeSessions);
    if (remaining.length > 0) setActiveSession(remaining[remaining.length - 1]);
    else { activeSessionId = null; _offerCloseTunnels(); }
    return;
  }
  const delay = Math.min(500 * Math.pow(2, attempt - 1), 16000);
  if (session.term) session.term.write('\r\n\x1b[33m' + I18N.term_reconnecting.replace('{{n}}', attempt).replace('{{m}}', maxAttempts) + '\x1b[0m\r\n');
  setTimeout(() => {
    if (!activeSessions[sid] || activeSessions[sid]._intentionalClose) return;
    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(wsProto + '//' + location.host + '/ws/' + sid);
    activeSessions[sid].ws = ws;
    ws.onmessage = e => {
      const msg = JSON.parse(e.data);
      if (msg[0] === 'stdout' && activeSessions[sid]?.term) activeSessions[sid].term.write(msg[1]);
    };
    ws.onopen = () => {
      const s = activeSessions[sid];
      if (s && s.term) s.term.write('\x1b[32m' + I18N.term_reconnect_ok + '\x1b[0m\r\n');
      if (s && s.term && s.fitAddon) ws.send(JSON.stringify(['set_size', s.term.rows, s.term.cols]));
    };
    ws.onclose = () => {
      const s = activeSessions[sid];
      if (s && !s._intentionalClose) _reconnectWs(sid, attempt + 1);
    };
  }, delay);
}
function closeTab(sid) {
  const session = activeSessions[sid];
  if (session) {
    session._intentionalClose = true;
    if (session.term) session.term.dispose();
    if (session.pane) session.pane.remove();
    session.ws.close();
  }
  delete activeSessions[sid];
  removeTab(sid);
  const remaining = Object.keys(activeSessions);
  if (remaining.length > 0) {
    setActiveSession(remaining[remaining.length - 1]);
  } else {
    activeSessionId = null;
    _offerCloseTunnels();
  }
  apiFetch('DELETE', '/api/agent/session/' + sid);
}
async function _offerCloseTunnels() {
  const data = await apiFetch('GET', '/api/tunnel/status');
  if (!data || !data.tunnels || data.tunnels.length === 0) return;
  const portList = data.tunnels.map(t => `${I18N.tunnel_local}${t.port}`).join(', ');
  const confirmMsg = I18N.msg_all_sessions_closed + '\n' + I18N.msg_close_tunnels_q.replace('{{ports}}', portList);
  if (confirm(confirmMsg)) {
    await apiFetch('POST', '/api/tunnel/close', {all: true});
    loadTunnels();
    showToast(I18N.toast_tunnels_closed);
  }
}
function removeTab(sid) {
  const el = document.getElementById('tab-' + sid);
  if (el) el.remove();
}
function setActiveSession(sid) {
  if (ctrlActive || altActive || shiftActive) {
    ctrlActive = false; altActive = false; shiftActive = false;
    ['btn-ctrl','btn-alt','btn-shift'].forEach(id => { const el = document.getElementById(id); if (el) el.classList.remove('active'); });
    _updateModKeyRow();
  }
  activeSessionId = sid;
  document.querySelectorAll('.term-tab').forEach(el => el.classList.remove('active'));
  const tab = document.getElementById('tab-' + sid);
  if (tab) tab.classList.add('active');
  // 모든 pane 숨기고 현재 세션 pane만 표시
  document.querySelectorAll('[id^="term-pane-"]').forEach(p => p.style.display = 'none');
  const session = activeSessions[sid];
  if (session && session.pane) {
    session.pane.style.display = 'block';
    // pane이 표시된 후 fit + focus
    setTimeout(() => {
      if (session.fitAddon) {
        session.fitAddon.fit();
        session.ws.send(JSON.stringify(['set_size', session.term.rows, session.term.cols]));
      }
      if (session.term) session.term.focus();
    }, 10);
  }
}