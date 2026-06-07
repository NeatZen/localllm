// static/js/modelsPage.js — Models discovery page (hardware fit + popular models)

import uiModule from './ui.js';
import Modals from './modalManager.js';

let initialized = false;
let modalEl = null;
let _data = null;

function esc(s) { return uiModule.esc(s); }

const FIT_LABELS = {
  perfect: 'Perfect fit',
  good: 'Good fit',
  marginal: 'Tight fit',
  unknown: 'Unknown fit',
};

function _fitBadge(level) {
  const cls = ['perfect', 'good', 'marginal'].includes(level) ? level : 'unknown';
  return `<span class="models-page-fit models-page-fit-${cls}">${esc(FIT_LABELS[level] || level)}</span>`;
}

function _renderHardware(hw) {
  const chips = [];
  if (hw.has_gpu && hw.gpu_name) {
    const count = hw.gpu_count > 1 ? ` ×${hw.gpu_count}` : '';
    chips.push(`${esc(hw.gpu_name)}${count} · ${Math.round(hw.gpu_vram_gb)} GB VRAM`);
  } else {
    chips.push('No GPU detected — CPU mode');
  }
  if (hw.available_ram_gb) chips.push(`${Math.round(hw.available_ram_gb)} GB RAM`);
  if (hw.backend) chips.push(esc(String(hw.backend).toUpperCase()));
  return chips.map((c) => `<span class="models-page-chip">${c}</span>`).join('');
}

function _rowActionsBundled(model) {
  if (model.active) return '<span class="models-page-muted">In use</span>';
  if (model.installed) {
    return `<button type="button" class="admin-btn-sm models-page-use" data-model-id="${esc(model.id)}">Use</button>`;
  }
  const dl = model.download || {};
  if (dl.state === 'downloading') {
    return `<button type="button" class="admin-btn-sm" disabled>${dl.progress || 0}%</button>`;
  }
  return `<button type="button" class="admin-btn-sm models-page-download" data-model-id="${esc(model.id)}">Download</button>`;
}

function _section(title, subtitle, bodyHtml, id) {
  return `
    <section class="models-page-section" ${id ? `id="${id}"` : ''}>
      <div class="models-page-section-head">
        <h3>${esc(title)}</h3>
        ${subtitle ? `<p>${subtitle}</p>` : ''}
      </div>
      <div class="models-page-section-body">${bodyHtml}</div>
    </section>
  `;
}

function _bundledRows(models) {
  if (!models.length) return '<div class="models-page-empty">No built-in models in catalog</div>';
  return models.map((m) => {
    const meta = [
      m.size_gb ? `${m.size_gb} GB` : '',
      m.vram_gb ? `~${m.vram_gb} GB VRAM` : '',
    ].filter(Boolean).join(' · ');
    const dl = m.download || {};
    const progress = dl.state === 'downloading'
      ? `<div class="model-hub-progress"><div class="model-hub-progress-fill" style="width:${Math.min(100, dl.progress || 0)}%"></div></div>`
      : '';
    return `
      <div class="models-page-row">
        <div class="models-page-row-main">
          <div class="models-page-row-title">${esc(m.name)} ${_fitBadge(m.fit_level)}</div>
          <div class="models-page-row-desc">${esc(m.description || '')}</div>
          <div class="models-page-row-meta">${esc(meta)}</div>
          ${progress}
        </div>
        <div class="models-page-row-actions">${_rowActionsBundled(m)}</div>
      </div>
    `;
  }).join('');
}

function _hwfitRows(models) {
  if (!models.length) return '<div class="models-page-empty">No GGUF matches for your hardware. Try rescanning or check Cookbook for more options.</div>';
  return models.map((m) => {
    const short = (m.name || '').split('/').pop();
    const meta = [
      m.params_b ? `${m.params_b}B` : m.parameter_count || '',
      m.quant ? m.quant : '',
      m.required_gb ? `~${m.required_gb} GB` : '',
      m.speed_tps ? `~${m.speed_tps} tok/s` : '',
      m.score ? `score ${m.score}` : '',
    ].filter(Boolean).join(' · ');
    const repo = m.gguf_repo || '';
    return `
      <div class="models-page-row models-page-row-hwfit" data-gguf-repo="${esc(repo)}">
        <div class="models-page-row-main">
          <div class="models-page-row-title">${esc(short)} ${_fitBadge(m.fit_level)}</div>
          <div class="models-page-row-meta">${esc(meta)}</div>
          ${repo ? `<div class="models-page-row-repo">${esc(repo)}</div>` : ''}
        </div>
        <div class="models-page-row-actions">
          ${repo ? `<button type="button" class="admin-btn-sm models-page-cookbook" data-repo="${esc(repo)}">Get model</button>` : ''}
        </div>
      </div>
    `;
  }).join('');
}

function _hfRows(models, emptyMsg) {
  if (!models.length) return `<div class="models-page-empty">${esc(emptyMsg)}</div>`;
  return models.map((m) => {
    const short = (m.repo_id || '').split('/').pop();
    const org = (m.repo_id || '').includes('/') ? m.repo_id.split('/')[0] : '';
    const meta = [
      org,
      m.needed_vram_gb ? `~${m.needed_vram_gb} GB VRAM` : '',
      m.downloads ? `${Number(m.downloads).toLocaleString()} downloads` : '',
      m.likes ? `${Number(m.likes).toLocaleString()} likes` : '',
    ].filter(Boolean).join(' · ');
    return `
      <div class="models-page-row models-page-row-hf" data-repo="${esc(m.repo_id)}">
        <div class="models-page-row-main">
          <div class="models-page-row-title">
            ${esc(short)}
            <a href="https://huggingface.co/${esc(m.repo_id)}" target="_blank" rel="noopener" class="models-page-hf-link">HF ↗</a>
          </div>
          <div class="models-page-row-meta">${esc(meta)}</div>
        </div>
        <div class="models-page-row-actions">
          <button type="button" class="admin-btn-sm models-page-cookbook" data-repo="${esc(m.repo_id)}">Get model</button>
        </div>
      </div>
    `;
  }).join('');
}

function _renderPage(data) {
  const root = document.getElementById('models-page-content');
  if (!root) return;
  const hw = data.hardware || {};
  const err = data.errors || {};

  root.innerHTML = `
    <div class="models-page-hardware">
      <div class="models-page-hardware-title">Your machine</div>
      <div class="models-page-chips">${_renderHardware(hw)}</div>
      <button type="button" class="admin-btn-sm" id="models-page-rescan">Rescan hardware</button>
    </div>
    ${_section(
      'Built-in AI — quick installs',
      'One-click downloads for the built-in local AI (no Cookbook setup).',
      _bundledRows(data.bundled || []),
      'models-page-bundled'
    )}
    ${_section(
      'Best for your hardware',
      'GGUF models ranked for your GPU and RAM from the Odysseus catalog.',
      _hwfitRows(data.best_for_hardware || []),
    )}
    ${_section(
      'Trending on Hugging Face',
      err.trending ? `Note: ${esc(err.trending)}` : 'Popular models right now that fit your VRAM.',
      _hfRows(data.trending || [], 'No trending models matched your hardware filter.'),
    )}
    ${_section(
      'Most downloaded',
      'Top downloaded chat models on Hugging Face (VRAM filtered).',
      _hfRows(data.most_downloaded || [], 'No download leaders matched your hardware filter.'),
    )}
  `;

  root.querySelector('#models-page-rescan')?.addEventListener('click', () => refresh(true));

  root.querySelectorAll('.models-page-download').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.modelId;
      if (!id) return;
      btn.disabled = true;
      try {
        const res = await fetch('/api/model-hub/download', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model_id: id }),
        });
        const out = await res.json();
        if (!res.ok || !out.ok) throw new Error(out.error || 'Download failed');
        uiModule.showToast('Download started');
        await refresh(true);
        if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
      } catch (e) {
        uiModule.showError(e.message || 'Download failed');
        btn.disabled = false;
      }
    });
  });

  root.querySelectorAll('.models-page-use').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.modelId;
      if (!id) return;
      btn.disabled = true;
      try {
        const res = await fetch('/api/model-hub/activate', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model_id: id }),
        });
        const out = await res.json();
        if (!res.ok || !out.ok) throw new Error(out.error || 'Switch failed');
        uiModule.showToast('Model activated');
        await refresh(true);
        if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
        if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
      } catch (e) {
        uiModule.showError(e.message || 'Switch failed');
        btn.disabled = false;
      }
    });
  });

  root.querySelectorAll('.models-page-cookbook').forEach((btn) => {
    btn.addEventListener('click', () => {
      const repo = btn.dataset.repo;
      if (!repo) return;
      if (window.cookbookModule?.open) {
        window.cookbookModule.open({ tab: 'Download' }).then(() => {
          const input = document.getElementById('cookbook-dl-repo');
          if (input) {
            input.value = repo;
            input.focus();
          }
        }).catch(() => {});
      } else {
        navigator.clipboard?.writeText(repo);
        uiModule.showToast('Repo copied — paste in Cookbook download');
      }
      close();
    });
  });
}

export async function refresh(forceFresh = false) {
  const root = document.getElementById('models-page-content');
  if (!root) return;
  root.innerHTML = '<div class="models-page-loading">Scanning your hardware and loading models…</div>';
  try {
    const res = await fetch(`/api/models-page/discover?fresh=${forceFresh ? 'true' : 'false'}`, {
      credentials: 'same-origin',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _data = await res.json();
    _renderPage(_data);
  } catch (e) {
    root.innerHTML = `<div class="models-page-empty">Could not load models page: ${esc(e.message)}</div>`;
  }
}

function init() {
  if (initialized) return;
  modalEl = document.getElementById('models-page-modal');
  if (!modalEl) return;

  const closeBtn = modalEl.querySelector('.close-btn');
  closeBtn?.addEventListener('click', close);

  Modals.register('models-page-modal', {
    railBtnId: null,
    sidebarBtnId: 'tool-models-page-btn',
    closeFn: () => close(),
    restoreFn: () => refresh(false),
  });

  const browseBtn = document.getElementById('models-page-browse-btn');
  browseBtn?.addEventListener('click', () => open());

  const toolBtn = document.getElementById('tool-models-page-btn');
  toolBtn?.addEventListener('click', () => open());

  initialized = true;
}

export function open() {
  init();
  if (!modalEl) return;
  modalEl.classList.remove('hidden');
  refresh(false);
}

export function close() {
  if (!modalEl) return;
  const content = modalEl.querySelector('.modal-content');
  if (content && !content.classList.contains('modal-closing')) {
    content.classList.add('modal-closing');
    content.addEventListener('animationend', () => {
      modalEl.classList.add('hidden');
      content.classList.remove('modal-closing');
    }, { once: true });
    setTimeout(() => {
      if (!modalEl.classList.contains('hidden')) {
        modalEl.classList.add('hidden');
        content.classList.remove('modal-closing');
      }
    }, 250);
  } else {
    modalEl.classList.add('hidden');
  }
}

const modelsPageModule = { open, close, refresh, init };
export default modelsPageModule;
window.modelsPageModule = modelsPageModule;
