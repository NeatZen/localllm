/**
 * Agent Workspace — sandboxed project folders with approval-gated changes.
 */

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { providerLogo } from './providers.js';

const API_BASE = window.location.origin;
let _open = false;
let _projects = [];
let _activeProjectId = null;
let _sessionId = null;
let _sessionModel = null;
let _sessionEndpoint = null;
let _pollTimer = null;
let _escHandler = null;
let _eventsWired = false;

async function _api(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
  return data;
}

function _el(id) {
  return document.getElementById(id);
}

function _escape(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function _activateAgentMode(opts = {}) {
  try {
    const mod = await import('./sessions.js');
    if (mod.activateAgentMode) mod.activateAgentMode(opts);
  } catch (e) {
    console.warn('activateAgentMode failed:', e);
  }
}

function _updatePendingBadge(count) {
  const dot = _el('workspace-pending-dot');
  if (dot) {
    dot.style.display = count > 0 ? 'inline-block' : 'none';
    dot.textContent = count > 99 ? '99+' : String(count);
  }
}

async function _loadProjects() {
  const data = await _api('/api/workspace/projects');
  _projects = data.projects || [];
  const list = _el('ws-project-list');
  if (!list) return;
  if (!_projects.length) {
    list.innerHTML = '<div class="ws-empty">No projects yet.<br><small>Click + Project to start.</small></div>';
    return;
  }
  list.innerHTML = _projects.map(p => `
    <button type="button" class="ws-project-item${p.id === _activeProjectId ? ' active' : ''}" data-id="${_escape(p.id)}">
      <span class="ws-project-name">${_escape(p.name)}</span>
      <span class="ws-project-slug">${_escape(p.slug)}</span>
    </button>
  `).join('');
  list.querySelectorAll('.ws-project-item').forEach(btn => {
    btn.addEventListener('click', () => _selectProject(btn.dataset.id));
  });
}

async function _ensureSession(projectId) {
  const sess = await _api(`/api/workspace/projects/${projectId}/ensure-session`, { method: 'POST' });
  _sessionId = sess.session_id || null;
  if (sess.model) _sessionModel = sess.model;
  if (sess.endpoint_url) _sessionEndpoint = sess.endpoint_url;
  _updateWsModelLabel(_sessionModel);
  return _sessionId;
}

function _wsModelDisplayName(modelId) {
  return modelId ? String(modelId).split(/[/\\]/).pop() : 'Select model';
}

function _updateWsModelLabel(modelId) {
  const label = _el('ws-model-picker-label');
  const wrap = _el('ws-model-picker-wrap');
  if (!label) return;
  const displayName = _wsModelDisplayName(modelId);
  const logo = modelId ? providerLogo(modelId) : null;
  if (logo) {
    label.innerHTML = '<span class="model-picker-logo">' + logo + '</span> ' + displayName;
  } else {
    label.textContent = displayName;
  }
  if (wrap) wrap.style.opacity = _sessionId ? '' : '0.45';
}

async function _refreshWsModelLabel() {
  if (!_sessionId) {
    _updateWsModelLabel(null);
    return;
  }
  if (_sessionModel) {
    _updateWsModelLabel(_sessionModel);
    return;
  }
  try {
    const mod = await import('./sessions.js');
    let sess = mod.getSessions?.()?.find(s => s.id === _sessionId);
    if (!sess?.model && mod.loadSessions) {
      await mod.loadSessions({ skipAutoSelect: true });
      sess = mod.getSessions?.()?.find(s => s.id === _sessionId);
    }
    if (sess?.model) {
      _sessionModel = sess.model;
      _sessionEndpoint = sess.endpoint_url || _sessionEndpoint;
    }
  } catch (e) {
    console.warn('workspace model label refresh:', e);
  }
  _updateWsModelLabel(_sessionModel);
}

function _getWsModels(filter) {
  const items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
  const result = [];
  const seen = new Set();
  const q = (filter || '').toLowerCase();
  items.forEach(item => {
    if (item.offline) return;
    const allModels = (item.models || []).concat(item.models_extra || []);
    const allDisplay = (item.models_display || []).concat(item.models_extra_display || []);
    allModels.forEach((mid, i) => {
      if (seen.has(mid)) return;
      const display = (allDisplay[i] || mid).split(/[/\\]/).pop();
      if (q && !mid.toLowerCase().includes(q) && !display.toLowerCase().includes(q)) return;
      seen.add(mid);
      result.push({
        mid,
        display,
        url: item.url,
        endpointId: item.endpoint_id,
        epName: item.endpoint_name || '',
      });
    });
  });
  return result;
}

function _populateWsModelPicker(filter) {
  const listEl = _el('ws-model-picker-list');
  if (!listEl) return;
  listEl.innerHTML = '';
  const all = _getWsModels(filter);
  if (!all.length) {
    listEl.innerHTML = '<div class="model-switch-empty">No models available</div>';
    return;
  }
  let favs = [];
  try { favs = JSON.parse(localStorage.getItem('odysseus-model-favorites') || '[]'); } catch { favs = []; }
  const favModels = all.filter(m => favs.includes(m.mid));
  const restModels = all.filter(m => !favs.includes(m.mid));
  const addSection = (label) => {
    const el = document.createElement('div');
    el.className = 'mp-section-label';
    el.textContent = label;
    listEl.appendChild(el);
  };
  const addRow = (m) => {
    const row = document.createElement('div');
    row.className = 'model-switch-item';
    const logo = providerLogo(m.mid);
    if (logo) {
      const logoSpan = document.createElement('span');
      logoSpan.className = 'provider-logo';
      logoSpan.style.opacity = '0.6';
      logoSpan.innerHTML = logo;
      row.appendChild(logoSpan);
    }
    const nameSpan = document.createElement('span');
    nameSpan.textContent = m.display;
    row.appendChild(nameSpan);
    const epSpan = document.createElement('span');
    epSpan.className = 'model-switch-ep';
    const epDisplay = m.epName && !m.display.toLowerCase().includes(m.epName.toLowerCase().split('/').pop())
      ? m.epName : '';
    epSpan.textContent = epDisplay;
    row.appendChild(epSpan);
    row.addEventListener('click', () => _pickWsModel(m));
    listEl.appendChild(row);
  };
  if (favModels.length) {
    addSection('Favorites');
    favModels.forEach(addRow);
  }
  if (restModels.length) {
    if (favModels.length) addSection('All models');
    restModels.forEach(addRow);
  }
}

async function _maybeActivateBundledModel(m) {
  const isBundled = (m.epName || '').toLowerCase().includes('built-in ai') || (m.url || '').includes(':11435');
  if (!isBundled) return true;
  try {
    const statusRes = await fetch('/api/model-hub/status', { credentials: 'same-origin' });
    if (!statusRes.ok) return true;
    const hub = await statusRes.json();
    const activeFile = (hub.active?.file || '').toLowerCase();
    const pickedFile = m.mid.split(/[/\\]/).pop().toLowerCase();
    if (!pickedFile || activeFile === pickedFile) return true;
    uiModule?.showToast?.(`Switching to ${m.display}...`);
    const actRes = await fetch('/api/model-hub/activate-by-path', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: m.mid }),
    });
    const act = await actRes.json().catch(() => ({}));
    if (!actRes.ok || !act.ok) {
      uiModule?.showError?.(act.error || 'Failed to switch built-in model');
      return false;
    }
    if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
    return true;
  } catch (e) {
    console.warn('workspace bundled model switch:', e);
    return true;
  }
}

async function _pickWsModel(m) {
  if (!_sessionId) {
    uiModule?.showToast?.('Select a project first', 'warn');
    return;
  }
  if (!(await _maybeActivateBundledModel(m))) return;
  const menu = _el('ws-model-picker-menu');
  const search = _el('ws-model-picker-search');
  if (menu) {
    menu.classList.add('hidden');
    menu.classList.remove('closing');
  }
  if (search) search.value = '';
  const fd = new FormData();
  fd.append('model', m.mid);
  fd.append('endpoint_url', m.url);
  if (m.endpointId) fd.append('endpoint_id', m.endpointId);
  try {
    const res = await fetch(`${API_BASE}/api/session/${_sessionId}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
    if (!res.ok) {
      uiModule?.showError?.('Failed to set agent model');
      return;
    }
    _sessionModel = m.mid;
    _sessionEndpoint = m.url;
    _updateWsModelLabel(_sessionModel);
    try {
      const mod = await import('./sessions.js');
      const sessions = mod.getSessions?.() || [];
      const s = sessions.find(x => x.id === _sessionId);
      if (s) {
        s.model = m.mid;
        s.endpoint_url = m.url;
      }
    } catch { /* sessions list optional */ }
    uiModule?.showToast?.(`Agent using ${m.display}`);
  } catch (e) {
    uiModule?.showError?.('Failed to set agent model: ' + e.message);
  }
}

function _initWsModelPicker() {
  const btn = _el('ws-model-picker-btn');
  const menu = _el('ws-model-picker-menu');
  const search = _el('ws-model-picker-search');
  const wrap = _el('ws-model-picker-wrap');
  if (!btn || !menu || !search || !wrap || btn.dataset.wired) return;
  btn.dataset.wired = '1';

  const closeMenu = () => {
    if (menu.classList.contains('hidden')) return;
    menu.classList.add('closing');
    menu.addEventListener('animationend', function onDone() {
      menu.removeEventListener('animationend', onDone);
      menu.classList.remove('closing');
      menu.classList.add('hidden');
      search.value = '';
    }, { once: true });
    setTimeout(() => {
      if (!menu.classList.contains('hidden')) {
        menu.classList.remove('closing');
        menu.classList.add('hidden');
        search.value = '';
      }
    }, 200);
  };

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!_sessionId) {
      uiModule?.showToast?.('Select a project first', 'warn');
      return;
    }
    if (menu.classList.contains('hidden') || menu.classList.contains('closing')) {
      menu.classList.remove('closing', 'hidden');
      _populateWsModelPicker('');
      if (window.innerWidth >= 768) search.focus();
    } else {
      closeMenu();
    }
  });

  search.addEventListener('input', () => _populateWsModelPicker(search.value));
  search.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', (e) => {
    if (!menu.classList.contains('hidden') && !menu.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
      closeMenu();
    }
  });
}

async function _selectProject(projectId) {
  _activeProjectId = projectId;
  _loadProjects().catch(() => {});
  const proj = _projects.find(p => p.id === projectId);
  const title = _el('ws-project-title');
  if (title) title.textContent = proj ? proj.name : 'Project';
  try {
    await _ensureSession(projectId);
  } catch (e) {
    _sessionId = proj?.session_id || null;
    _sessionModel = null;
    _sessionEndpoint = null;
    uiModule?.showToast?.(e.message || 'Could not bind agent session', 'error');
    console.warn('ensure-session:', e);
  }
  await _refreshWsModelLabel();
  await Promise.all([_loadTree(), _loadChanges(), _loadActivity()]);
}

async function _loadTree() {
  const treeEl = _el('ws-file-tree');
  if (!treeEl || !_activeProjectId) return;
  treeEl.innerHTML = '<div class="ws-muted">Loading…</div>';
  try {
    const data = await _api(`/api/workspace/projects/${_activeProjectId}/tree`);
    treeEl.innerHTML = _renderTree(data.tree || [], '');
  } catch (e) {
    treeEl.innerHTML = `<div class="ws-error">${_escape(e.message)}</div>`;
  }
}

function _renderTree(nodes, indent) {
  if (!nodes || !nodes.length) return indent ? '' : '<div class="ws-muted">Empty project</div>';
  return nodes.map(n => {
    if (n.type === 'dir') {
      return `<div class="ws-tree-dir" style="padding-left:${indent}px">
        <span class="ws-tree-label">📁 ${_escape(n.name)}</span>
        ${_renderTree(n.children || [], indent + 12)}
      </div>`;
    }
    return `<button type="button" class="ws-tree-file" data-path="${_escape(n.path)}" style="padding-left:${indent + 4}px">
      📄 ${_escape(n.name)}
    </button>`;
  }).join('');
}

async function _previewFile(path) {
  const pre = _el('ws-file-preview');
  if (!pre || !_activeProjectId) return;
  pre.textContent = 'Loading…';
  try {
    const data = await _api(`/api/workspace/projects/${_activeProjectId}/file?path=${encodeURIComponent(path)}`);
    pre.textContent = data.content || '(empty)';
  } catch (e) {
    pre.textContent = `Error: ${e.message}`;
  }
}

async function _loadChanges() {
  const box = _el('ws-changes-list');
  if (!box || !_activeProjectId) return;
  try {
    const data = await _api(`/api/workspace/projects/${_activeProjectId}/changes`);
    const changes = (data.changes || []).filter(c => c.status === 'pending');
    _updatePendingBadge(changes.length);
    if (!changes.length) {
      box.innerHTML = '<div class="ws-muted">No pending changes</div>';
      return;
    }
    box.innerHTML = changes.map(c => {
      const badge = c.action_type === 'run' ? 'RUN' : (c.action_type || 'file').toUpperCase();
      const target = c.path || (c.payload?.command || '');
      const diff = c.diff_preview ? `<pre class="ws-diff">${_escape(c.diff_preview)}</pre>` : '';
      const test = c.test_summary ? `<div class="ws-test-badge">${_escape(c.test_summary)}</div>` : '';
      return `<div class="ws-change-card" data-id="${_escape(c.id)}">
        <div class="ws-change-head"><span class="ws-badge">${badge}</span> ${_escape(c.summary || target)}</div>
        <div class="ws-change-path">${_escape(target)}</div>
        ${diff}
        ${test}
        <div class="ws-change-actions">
          <button type="button" class="ws-btn ws-btn-approve" data-approve="${_escape(c.id)}">Approve</button>
          <button type="button" class="ws-btn ws-btn-reject" data-reject="${_escape(c.id)}">Reject</button>
        </div>
      </div>`;
    }).join('');
    box.querySelectorAll('[data-approve]').forEach(btn => {
      btn.addEventListener('click', () => _approveChange(btn.dataset.approve));
    });
    box.querySelectorAll('[data-reject]').forEach(btn => {
      btn.addEventListener('click', () => _rejectChange(btn.dataset.reject));
    });
  } catch (e) {
    box.innerHTML = `<div class="ws-error">${_escape(e.message)}</div>`;
  }
}

async function _loadActivity() {
  const box = _el('ws-activity-log');
  if (!box || !_activeProjectId) return;
  try {
    const data = await _api(`/api/workspace/projects/${_activeProjectId}/activity?limit=80`);
    const rows = data.activity || [];
    if (!rows.length) {
      box.innerHTML = '<div class="ws-muted">No activity yet</div>';
      return;
    }
    box.innerHTML = rows.map(a => {
      const kind = (a.kind || 'log').toUpperCase();
      const meta = a.meta?.stdout || a.meta?.stderr || '';
      const test = a.meta?.test_summary ? ` [${a.meta.test_summary}]` : '';
      return `<div class="ws-activity-row">
        <span class="ws-act-kind">${_escape(kind)}</span>
        <span class="ws-act-msg">${_escape(a.message || '')}${_escape(test)}</span>
        ${meta ? `<pre class="ws-act-out">${_escape(String(meta).slice(0, 2000))}</pre>` : ''}
      </div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.innerHTML = `<div class="ws-error">${_escape(e.message)}</div>`;
  }
}

async function _approveChange(id) {
  await _api(`/api/workspace/changes/${id}/approve`, { method: 'POST' });
  await _loadChanges();
  await _loadTree();
  await _loadActivity();
}

async function _rejectChange(id) {
  const reason = window.prompt('Rejection reason (optional):') || '';
  await _api(`/api/workspace/changes/${id}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
  await _loadChanges();
}

async function _approveAll() {
  if (!_activeProjectId) return;
  await _api(`/api/workspace/projects/${_activeProjectId}/approve-all`, { method: 'POST' });
  await _loadChanges();
  await _loadTree();
  await _loadActivity();
}

async function _proposeShortcut(command, summary) {
  if (!_activeProjectId) {
    uiModule?.showToast?.('Select a project first', 'warn');
    return;
  }
  await _api(`/api/workspace/projects/${_activeProjectId}/propose-command`, {
    method: 'POST',
    body: JSON.stringify({ command, summary }),
  });
  await _loadChanges();
  uiModule?.showToast?.('Command queued for approval', 'info');
}

async function _browserTest() {
  const url = (_el('ws-browser-url')?.value || '').trim();
  if (!url || !_activeProjectId) return;
  try {
    const data = await _api(`/api/workspace/projects/${_activeProjectId}/browser-test`, {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
    if (data.ok) {
      uiModule?.showToast?.('Browser test started', 'info');
    } else {
      uiModule?.showToast?.(data.error || 'Browser test failed', 'error');
    }
    await _loadActivity();
  } catch (e) {
    uiModule?.showToast?.(e.message, 'error');
  }
}

async function _createProject() {
  const name = window.prompt('Project name:');
  if (!name || !name.trim()) return;
  try {
    const data = await _api('/api/workspace/projects', {
      method: 'POST',
      body: JSON.stringify({ name: name.trim() }),
    });
    await _loadProjects();
    if (data.project?.id) await _selectProject(data.project.id);
  } catch (e) {
    uiModule?.showToast?.(e.message, 'error');
  }
}

async function _deleteProject() {
  if (!_activeProjectId) return;
  if (!window.confirm('Delete this project and all its files?')) return;
  await _api(`/api/workspace/projects/${_activeProjectId}`, { method: 'DELETE' });
  _activeProjectId = null;
  _sessionId = null;
  _sessionModel = null;
  _sessionEndpoint = null;
  _el('ws-file-tree') && (_el('ws-file-tree').innerHTML = '');
  _el('ws-file-preview') && (_el('ws-file-preview').textContent = '');
  _el('ws-changes-list') && (_el('ws-changes-list').innerHTML = '');
  _el('ws-activity-log') && (_el('ws-activity-log').innerHTML = '');
  const chatLog = _el('ws-chat-log');
  if (chatLog) chatLog.innerHTML = '<div class="ws-muted">Select a project to start building.</div>';
  await _loadProjects();
}

function _appendWsChat(role, text, extraClass) {
  const log = _el('ws-chat-log');
  if (!log) return null;
  if (log.querySelector('.ws-muted')) log.innerHTML = '';
  const row = document.createElement('div');
  row.className = `ws-chat-msg ws-chat-${role}${extraClass ? ' ' + extraClass : ''}`;
  row.textContent = text;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return row;
}

async function _sendWorkspaceChatMessage(rawText) {
  const text = (rawText || '').trim();
  if (!text) return;
  if (!_activeProjectId) {
    uiModule?.showToast?.('Select a project first', 'warn');
    return;
  }
  try {
    await _ensureSession(_activeProjectId);
  } catch (e) {
    uiModule?.showToast?.(e.message || 'Could not bind agent session', 'error');
    return;
  }
  if (!_sessionId) {
    uiModule?.showToast?.('No agent session for this project', 'warn');
    return;
  }

  const proj = _projects.find(p => p.id === _activeProjectId);
  try {
    const mod = await import('./sessions.js');
    if (mod.bindWorkspaceChatContext) {
      mod.bindWorkspaceChatContext(_activeProjectId, proj?.name);
    } else {
      await _activateAgentMode({ workspace: true });
    }
  } catch {
    await _activateAgentMode({ workspace: true });
  }

  _appendWsChat('user', text);
  const input = _el('ws-chat-input');
  if (input) input.value = '';

  const assistantRow = _appendWsChat('assistant', 'Working…', 'ws-chat-streaming');
  let assistantText = '';
  let toolCount = 0;
  let streamError = '';

  const fd = new FormData();
  fd.append('message', text);
  fd.append('session', _sessionId);
  fd.append('mode', 'agent');
  fd.append('allow_bash', 'true');

  let res;
  try {
    res = await fetch(`${API_BASE}/api/chat_stream`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
    });
  } catch (e) {
    if (assistantRow) assistantRow.textContent = `Error: ${e.message}`;
    return;
  }
  if (!res.ok) {
    const errBody = await res.text().catch(() => '');
    if (assistantRow) assistantRow.textContent = `Error: ${errBody.slice(0, 200) || res.statusText}`;
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const _applyAssistant = () => {
    if (!assistantRow) return;
    let text = assistantText.trim();
    if (streamError) {
      text = streamError;
    } else if (!text && toolCount > 0) {
      text = `Used ${toolCount} tool(s). Check Pending changes on the right — approve files/commands to apply them.`;
    } else if (!text) {
      text = 'No file changes proposed. The model may need another try — use Agent mode with a tool-capable model, or ask again more specifically (e.g. "create index.html with…").';
    }
    assistantRow.textContent = text;
    assistantRow.classList.remove('ws-chat-streaming');
  };

  const _handleWsEvent = (json) => {
    if (json.delta) {
      assistantText += json.delta;
      if (assistantRow) {
        assistantRow.textContent = assistantText;
        assistantRow.classList.add('ws-chat-streaming');
      }
      return;
    }
    const t = json.type;
    if (t === 'tool_start') {
      toolCount += 1;
      const tool = json.tool || 'tool';
      const cmd = json.command ? `: ${String(json.command).slice(0, 80)}` : '';
      _appendWsChat('tool', `${tool}${cmd}`);
      if (assistantRow && !assistantText.trim()) {
        assistantRow.textContent = `Running ${tool}…`;
      }
    } else if (t === 'tool_output') {
      const ok = json.exit_code === 0 || json.exit_code === undefined;
      const out = (json.output || '').slice(0, 240);
      _appendWsChat('tool', `${json.tool || 'tool'} ${ok ? 'done' : 'failed'}${out ? ': ' + out : ''}`);
      if (['write_file', 'propose_file_change', 'bash', 'python', 'propose_command'].includes(json.tool)) {
        _loadChanges().catch(() => {});
        _loadTree().catch(() => {});
        _loadActivity().catch(() => {});
      }
    } else if (t === 'agent_step') {
      if (assistantRow && !assistantText.trim()) {
        assistantRow.textContent = json.round ? `Agent step ${json.round}…` : 'Working…';
      }
    } else if (t === 'budget_exceeded') {
      streamError = `Tool limit reached (${json.used || '?'} calls). Approve pending changes or start a new message.`;
    } else if (json.error) {
      streamError = String(json.error).slice(0, 300);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data: '));
        if (!line || line === 'data: [DONE]') continue;
        let json;
        try { json = JSON.parse(line.slice(6)); } catch { continue; }
        _handleWsEvent(json);
      }
    }
  } catch (e) {
    streamError = streamError || e.message;
  } finally {
    _applyAssistant();
    await Promise.all([_loadChanges(), _loadTree(), _loadActivity()]);
  }
}

async function _openInChat() {
  if (!_activeProjectId) {
    uiModule?.showToast?.('Select a project first', 'warn');
    return;
  }
  try {
    await _ensureSession(_activeProjectId);
  } catch (e) {
    uiModule?.showToast?.(e.message || 'Could not create agent session', 'error');
    return;
  }
  if (!_sessionId) {
    uiModule?.showToast?.('No agent session for this project', 'warn');
    return;
  }
  try {
    window._activeWorkspaceProjectId = _activeProjectId;
    const proj = _projects.find(p => p.id === _activeProjectId);
    const mod = await import('./sessions.js');
    if (mod.bindWorkspaceChatContext) {
      mod.bindWorkspaceChatContext(_activeProjectId, proj?.name);
    } else {
      await _activateAgentMode({ workspace: true });
    }
    // Select workspace session FIRST — loadSessions can auto-switch to another chat
    if (mod.selectSession) {
      await mod.selectSession(_sessionId);
    } else if (mod.setCurrentSessionId) {
      mod.setCurrentSessionId(_sessionId);
    }
    if (mod.loadSessions) {
      await mod.loadSessions({ skipAutoSelect: true });
      // Re-select after list refresh in case meta was missing on first pass
      if (mod.selectSession) await mod.selectSession(_sessionId);
    }
    // Minimize workspace to dock chip — keep approvals visible while chatting
    if (Modals.isRegistered('workspace-modal')) {
      Modals.minimize('workspace-modal');
    } else {
      const modal = _el('workspace-modal');
      if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
      }
      _open = false;
    }
    const projName = proj?.name;
    const msgInput = _el('message');
    if (msgInput) {
      msgInput.placeholder = `Agent workspace — tell me what to build in "${projName || 'this project'}"…`;
      msgInput.focus();
    }
    uiModule?.showToast?.(
      `Agent mode active for "${projName || 'project'}". Ask me to build something — I'll create files for you to approve.`,
      'info'
    );
  } catch (e) {
    uiModule?.showToast?.(e.message, 'error');
  }
}

function _wireEvents() {
  if (_eventsWired) return;
  _eventsWired = true;

  _el('ws-new-project')?.addEventListener('click', () => _createProject().catch(e => uiModule?.showToast?.(e.message, 'error')));
  _el('ws-delete-project')?.addEventListener('click', () => _deleteProject().catch(e => uiModule?.showToast?.(e.message, 'error')));
  _el('ws-approve-all')?.addEventListener('click', () => _approveAll().catch(e => uiModule?.showToast?.(e.message, 'error')));
  _el('ws-open-chat')?.addEventListener('click', () => _openInChat());
  _el('ws-chat-send')?.addEventListener('click', () => {
    _sendWorkspaceChatMessage(_el('ws-chat-input')?.value).catch(e => uiModule?.showToast?.(e.message, 'error'));
  });
  _el('ws-chat-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      _sendWorkspaceChatMessage(e.target.value).catch(err => uiModule?.showToast?.(err.message, 'error'));
    }
  });
  _el('ws-cmd-build')?.addEventListener('click', () => _proposeShortcut('npm run build', 'Run build'));
  _el('ws-cmd-test')?.addEventListener('click', () => _proposeShortcut('npm test', 'Run tests'));
  _el('ws-cmd-dev')?.addEventListener('click', () => _proposeShortcut('npm run dev', 'Start dev server'));
  _el('ws-browser-test')?.addEventListener('click', () => _browserTest());
  _el('close-workspace-modal')?.addEventListener('click', () => close());
  _initWsModelPicker();

  _el('ws-file-tree')?.addEventListener('click', e => {
    const btn = e.target.closest('.ws-tree-file');
    if (btn?.dataset.path) _previewFile(btn.dataset.path);
  });

  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(() => {
    if (_open && _activeProjectId) {
      _loadChanges().catch(() => {});
    }
  }, 4000);
}

function _injectStyles() {
  if (document.getElementById('workspace-styles')) return;
  const style = document.createElement('style');
  style.id = 'workspace-styles';
  style.textContent = `
    .workspace-body { display:grid; grid-template-columns:170px 1fr 300px; gap:8px; height:calc(100% - 4px); min-height:0; }
    .ws-col { display:flex; flex-direction:column; min-height:0; overflow:hidden; border:1px solid var(--border); border-radius:6px; background:var(--panel, var(--bg)); }
    .ws-col-main { min-height:0; }
    .ws-files-pane { flex:0 0 28%; max-height:28%; min-height:80px; }
    .ws-preview-pane { flex:0 0 22%; max-height:22%; min-height:60px; }
    .ws-chat-pane { flex:1; display:flex; flex-direction:column; min-height:120px; padding:0 !important; }
    .ws-chat-log { flex:1; overflow:auto; padding:6px; min-height:0; }
    .ws-chat-msg { font-size:11px; margin-bottom:6px; padding:6px 8px; border-radius:6px; white-space:pre-wrap; word-break:break-word; }
    .ws-chat-user { background:var(--border); opacity:0.95; }
    .ws-chat-assistant { background:rgba(255,255,255,0.04); border:1px solid var(--border); }
    .ws-chat-tool { font-size:10px; opacity:0.75; font-family:var(--mono, monospace); padding:4px 6px; }
    .ws-chat-streaming { opacity:0.85; }
    .ws-chat-input-row { display:flex; gap:6px; padding:6px; border-top:1px solid var(--border); }
    .ws-chat-input-row textarea { flex:1; font-size:11px; padding:6px 8px; border:1px solid var(--border); border-radius:6px; background:var(--input-bg, var(--bg)); color:inherit; resize:none; font-family:inherit; }
    .ws-btn-send { align-self:flex-end; padding:6px 12px; font-weight:600; border-color:var(--accent, #4a9); }
    .ws-col-head { padding:6px 8px; font-size:11px; font-weight:600; opacity:0.7; border-bottom:1px solid var(--border); display:flex; align-items:center; gap:6px; }
    .ws-chat-head { justify-content:space-between; gap:8px; }
    .ws-model-picker-wrap { margin-left:auto; flex-shrink:0; }
    .ws-model-picker-wrap .model-picker-btn { font-size:10px; padding:2px 6px; max-width:160px; }
    .ws-model-picker-wrap .model-picker-menu { right:0; left:auto; min-width:220px; max-width:min(320px, 90vw); }
    .ws-col-body { flex:1; overflow:auto; padding:6px; min-height:0; }
    .ws-project-item { display:block; width:100%; text-align:left; padding:6px 8px; border:none; background:transparent; color:inherit; cursor:pointer; border-radius:4px; margin-bottom:2px; }
    .ws-project-item:hover, .ws-project-item.active { background:var(--border); }
    .ws-project-name { display:block; font-size:12px; }
    .ws-project-slug { display:block; font-size:10px; opacity:0.5; }
    .ws-tree-file { display:block; width:100%; text-align:left; border:none; background:transparent; color:inherit; cursor:pointer; font-size:11px; padding:2px 4px; border-radius:3px; }
    .ws-tree-file:hover { background:var(--border); }
    .ws-tree-label { font-size:11px; opacity:0.85; }
    #ws-file-preview { font-family:var(--mono, monospace); font-size:11px; white-space:pre-wrap; margin:0; min-height:120px; max-height:40vh; overflow:auto; }
    .ws-change-card { border:1px solid var(--border); border-radius:6px; padding:8px; margin-bottom:6px; font-size:11px; }
    .ws-change-head { font-weight:600; margin-bottom:4px; }
    .ws-change-path { opacity:0.6; font-size:10px; margin-bottom:4px; word-break:break-all; }
    .ws-badge { font-size:9px; padding:1px 4px; border-radius:3px; background:var(--accent, #666); color:#fff; }
    .ws-diff, .ws-act-out { font-size:10px; max-height:100px; overflow:auto; background:var(--code-bg, rgba(0,0,0,0.2)); padding:4px; border-radius:4px; margin:4px 0; white-space:pre-wrap; }
    .ws-change-actions { display:flex; gap:6px; margin-top:6px; }
    .ws-btn { font-size:10px; padding:3px 8px; border-radius:4px; border:1px solid var(--border); cursor:pointer; background:var(--bg); color:inherit; }
    .ws-btn-approve { border-color: var(--accent, #4a9); }
    .ws-btn-reject { opacity:0.7; }
    .ws-muted { opacity:0.5; font-size:11px; padding:8px; }
    .ws-error { color: var(--accent-error, #c44); font-size:11px; }
    .ws-shortcuts { display:flex; flex-wrap:wrap; gap:4px; padding:4px 0; }
    .ws-shortcuts .ws-btn { font-size:10px; }
    .ws-browser-row { display:flex; gap:4px; margin-top:4px; }
    .ws-browser-row input { flex:1; font-size:11px; padding:4px 6px; border:1px solid var(--border); border-radius:4px; background:var(--input-bg, var(--bg)); color:inherit; }
    .ws-activity-row { margin-bottom:6px; font-size:10px; border-bottom:1px solid var(--border); padding-bottom:4px; }
    .ws-act-kind { font-size:9px; opacity:0.6; margin-right:4px; }
    #workspace-pending-dot { display:none; min-width:14px; height:14px; line-height:14px; text-align:center; font-size:9px; border-radius:7px; background:var(--accent-error, #c44); color:#fff; margin-left:4px; }
    .workspace-chat-banner { display:flex; align-items:center; gap:8px; padding:8px 12px; margin:0 8px 6px; border:1px solid var(--border); border-radius:8px; background:var(--panel, rgba(255,255,255,0.03)); font-size:12px; }
    .ws-chat-banner-text { flex:1; min-width:0; }
    .ws-chat-banner-hint { opacity:0.55; font-size:11px; }
    .ws-chat-banner-btn { font-size:11px; padding:4px 10px; border-radius:6px; border:1px solid var(--border); background:var(--bg); color:inherit; cursor:pointer; white-space:nowrap; }
  `;
  document.head.appendChild(style);
}

export function isOpen() {
  return _open;
}

export function open() {
  if (Modals.isRegistered('workspace-modal') && Modals.isMinimized('workspace-modal')) {
    Modals.restore('workspace-modal');
    return;
  }
  const modal = _el('workspace-modal');
  if (!modal) return;
  if (_open) return;
  _open = true;
  _injectStyles();
  modal.classList.remove('hidden');
  modal.style.display = '';
  _el('tool-workspace-btn')?.classList.add('active');
  makeWindowDraggable(modal);
  Modals.register('workspace-modal', {
    railBtnId: 'rail-workspace',
    sidebarBtnId: 'tool-workspace-btn',
    closeFn: close,
    restoreFn: () => {
      modal.classList.remove('hidden');
      modal.style.display = '';
    },
  });
  _wireEvents();
  _loadProjects().then(() => {
    if (_projects.length && !_activeProjectId) _selectProject(_projects[0].id);
    else if (_activeProjectId) _selectProject(_activeProjectId);
  }).catch(e => uiModule?.showToast?.(e.message, 'error'));

  _escHandler = e => {
    if (e.key === 'Escape' && _open) {
      e.preventDefault();
      close();
    }
  };
  document.addEventListener('keydown', _escHandler, true);
}

export function close() {
  if (!_open) return;
  _open = false;
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler, true);
    _escHandler = null;
  }
  const modal = _el('workspace-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
  _el('tool-workspace-btn')?.classList.remove('active');
  try { Modals.unregister('workspace-modal'); } catch {}
}

export default { open, close, isOpen };
