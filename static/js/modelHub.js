// static/js/modelHub.js — Model Download Hub (bundled local GGUF models)

import uiModule from './ui.js';

let pollTimer = null;
let initialized = false;

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

function formatGb(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return '';
  return v < 10 ? `${v.toFixed(1)} GB` : `${Math.round(v)} GB`;
}

function renderModelCard(model) {
  const dl = model.download || {};
  const isDownloading = dl.state === 'downloading';
  const hasError = dl.state === 'error';
  const progress = Math.max(0, Math.min(100, Number(dl.progress) || 0));
  const tags = (model.tags || []).map((t) => `<span class="model-hub-tag">${esc(t)}</span>`).join('');
  const meta = [
    model.size_gb ? formatGb(model.size_gb) : '',
    model.vram_gb ? `~${model.vram_gb} GB VRAM` : '',
  ].filter(Boolean).join(' · ');

  let statusLine = '';
  if (model.active) {
    statusLine = '<span class="model-hub-badge active">Active</span>';
  } else if (model.installed) {
    statusLine = '<span class="model-hub-badge installed">Downloaded</span>';
  } else if (isDownloading) {
    statusLine = `<span class="model-hub-badge downloading">${progress}%</span>`;
  } else if (hasError) {
    statusLine = '<span class="model-hub-badge error">Failed</span>';
  }

  let actions = '';
  const deleteBtn = model.installed
    ? `<button type="button" class="admin-btn-sm model-hub-delete" data-model-id="${esc(model.id)}">Delete</button>`
    : '';
  if (model.active) {
    actions = `<span class="model-hub-active-label">In use</span>${deleteBtn}`;
  } else if (isDownloading) {
    actions = '<button class="admin-btn-sm" disabled>Downloading...</button>';
  } else if (model.installed) {
    actions = `<button class="admin-btn-sm model-hub-use" data-model-id="${esc(model.id)}">Use Model</button>${deleteBtn}`;
  } else {
    actions = `<button class="admin-btn-sm model-hub-download" data-model-id="${esc(model.id)}">Download</button>`;
  }

  const progressBar = isDownloading
    ? `<div class="model-hub-progress"><div class="model-hub-progress-fill" style="width:${progress}%"></div></div>`
    : '';

  const errLine = hasError && dl.error
    ? `<div class="model-hub-error">${esc(dl.error)}</div>`
    : '';

  return `
    <div class="model-hub-row${model.active ? ' is-active' : ''}" data-model-id="${esc(model.id)}">
      <div class="model-hub-row-main">
        <div class="model-hub-row-head">
          <div class="model-hub-name">${esc(model.name || model.id)}</div>
          ${statusLine}
        </div>
        <div class="model-hub-desc">${esc(model.description || '')}</div>
        <div class="model-hub-meta">${esc(meta)}${tags ? ` <span class="model-hub-tags">${tags}</span>` : ''}</div>
        ${progressBar}
        ${errLine}
      </div>
      <div class="model-hub-actions">${actions}</div>
    </div>
  `;
}

function renderStatus(data) {
  const statusEl = el('model-hub-status');
  if (!statusEl) return;
  if (!data.enabled) {
    statusEl.textContent = 'Built-in AI is disabled on this install.';
    return;
  }
  const bundled = data.bundled || {};
  const active = data.active || {};
  if (bundled.healthy) {
    statusEl.textContent = `Built-in AI is running — active model: ${active.file || 'unknown'}`;
  } else if (bundled.state === 'starting') {
    statusEl.textContent = 'Built-in AI is starting (first load can take 1–2 minutes)...';
  } else if (bundled.state === 'downloading') {
    statusEl.textContent = bundled.message || 'Downloading model...';
  } else {
    statusEl.textContent = bundled.message || 'Built-in AI is not running yet.';
  }
}

function anyDownloading(catalog) {
  return (catalog || []).some((m) => (m.download || {}).state === 'downloading');
}

function schedulePoll(catalog) {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (!anyDownloading(catalog)) return;
  pollTimer = setTimeout(() => refresh(), 2000);
}

async function postAction(path, modelId) {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_id: modelId }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function bindActions() {
  if (initialized) return;
  initialized = true;
  const list = el('model-hub-list');
  if (!list) return;

  list.addEventListener('click', async (e) => {
    const dlBtn = e.target.closest('.model-hub-download');
    const useBtn = e.target.closest('.model-hub-use');
    const delBtn = e.target.closest('.model-hub-delete');
    const btn = dlBtn || useBtn || delBtn;
    if (!btn || btn.disabled) return;

    const modelId = btn.dataset.modelId;
    if (!modelId) return;

    if (delBtn) {
      const card = btn.closest('.model-hub-row');
      const name = card?.querySelector('.model-hub-name')?.textContent?.trim() || modelId;
      const isActive = card?.classList.contains('is-active');
      const msg = isActive
        ? `Delete "${name}"? Built-in AI will stop and switch to another model if one is installed.`
        : `Delete "${name}" from disk? This frees disk space and cannot be undone.`;
      if (!(await uiModule.styledConfirm(msg, { confirmText: 'Delete', danger: true }))) return;
    }

    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = delBtn ? 'Deleting...' : (dlBtn ? 'Starting...' : 'Switching...');

    try {
      if (dlBtn) {
        await postAction('/api/model-hub/download', modelId);
      } else if (useBtn) {
        await postAction('/api/model-hub/activate', modelId);
        if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
        if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
      } else {
        await postAction('/api/model-hub/delete', modelId);
        if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
        if (window.modelsPageModule?.refresh) await window.modelsPageModule.refresh(true);
      }
      await refresh();
    } catch (err) {
      console.error('Model hub action failed:', err);
      btn.disabled = false;
      btn.textContent = prev;
      uiModule.showError(err.message || 'Action failed');
    }
  });
}

export async function refresh() {
  const list = el('model-hub-list');
  if (!list) return;

  bindActions();

  try {
    const res = await fetch('/api/model-hub/status', { credentials: 'same-origin' });
    if (res.status === 401 || res.status === 403) {
      list.innerHTML = '<div class="admin-empty">Admin access required</div>';
      return;
    }
    if (!res.ok) {
      list.innerHTML = '<div class="admin-empty">Could not load model catalog</div>';
      return;
    }
    const data = await res.json();
    renderStatus(data);

    const catalog = data.catalog || [];
    if (!catalog.length) {
      list.innerHTML = '<div class="admin-empty">No models in catalog</div>';
      return;
    }

    list.innerHTML = catalog.map(renderModelCard).join('');
    schedulePoll(catalog);
  } catch (err) {
    console.error('Model hub refresh failed:', err);
    list.innerHTML = '<div class="admin-empty">Could not load model catalog</div>';
  }
}

const modelHubModule = { refresh };
export default modelHubModule;
