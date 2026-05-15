/* MemoryVault AI — Apple light redesign */
'use strict';

// ── State ──────────────────────────────────────────────────────────────────────
const S = {
  mode: 'guarded',             // 'local' | 'guarded' | 'hybrid'
  selectedSources: new Set(),
  conversation: [],            // [{role, content, meta, id}]
  vault: [],                   // full memory list from /vault/memories
  vaultFilter: 'all',
  search: '',
  serverStatus: { local: true, guardian: null, online: null },
  contextPanelOpen: false,
  pendingApproval: null,       // { query_id, query_text, preview }
  pairing: null,               // { token, url, expires_at, timer_id }
  lastMeta: null,
  _imeActive: false,
  mobileView: 'chat',
};

// ── API helpers ────────────────────────────────────────────────────────────────
const api = {
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return r.json();
  },
  async postForm(path, formData) {
    const r = await fetch(path, { method: 'POST', body: formData });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw Object.assign(new Error(err.detail || r.statusText), { status: r.status, data: err });
    }
    return r.json();
  },
  async get(path) {
    const r = await fetch(path);
    return r.json();
  },
  async del(path) {
    const r = await fetch(path, { method: 'DELETE' });
    return r.ok;
  },
};

// ── Utilities ──────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtDate(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function fileIcon(mime = '') {
  if (mime.includes('pdf'))   return '📄';
  if (mime.includes('image')) return '🖼️';
  if (mime.includes('text') || mime.includes('markdown')) return '📝';
  if (mime.includes('json') || mime.includes('javascript') || mime.includes('python') || mime.includes('code')) return '💻';
  return '📁';
}

function fileCategory(mime = '', filename = '') {
  const ext = filename.split('.').pop()?.toLowerCase() ?? '';
  if (mime.includes('pdf') || ext === 'pdf') return 'PDF';
  if (['js','ts','py','rb','go','rs','java','c','cpp','cs','swift','kt','sh'].includes(ext)
      || mime.includes('javascript') || mime.includes('python')) return 'Code';
  if (['md','txt','text','rst','org'].includes(ext) || mime.includes('text')) return 'Note';
  return 'Other';
}

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function routingBadge(routing) {
  if (!routing) return '';
  const map = {
    'local-only':             ['badge-local',    'Local'],
    'guarded-online':         ['badge-guarded',  'Guarded online'],
    'hybrid-knowledge-only':  ['badge-hybrid',   'Hybrid'],
    'approval-required':      ['badge-approval', 'Approval required'],
    'blocked':                ['badge-blocked',  'Blocked'],
  };
  const [cls, label] = map[routing] ?? ['badge-source', routing];
  return `<span class="badge ${cls}">${escHtml(label)}</span>`;
}

const MODE_HINTS = {
  local:   'Everything answered on this server only.',
  guarded: 'Private data stays local. Only sanitized context may be sent online.',
  hybrid:  'Local knowledge + online reasoning. Context is sanitized before sending.',
};

const MODE_PATHS = {
  local:   '/ask/local',
  guarded: '/ask',
  hybrid:  '/ask/online',
};

// ── Init ───────────────────────────────────────────────────────────────────────
function init() {
  renderModeSelector();
  renderChatThread();
  renderSourceChips();

  loadVault();
  pollStatus();
  setInterval(pollStatus, 30_000);

  setupIME();
  setupDragDrop();
  setupAutoResize();
  setupEventListeners();
}

// ── Server Status ──────────────────────────────────────────────────────────────
async function pollStatus() {
  try {
    const data = await api.get('/api/status');
    S.serverStatus = data;
    renderServerStatus();
  } catch (_) {
    S.serverStatus = { local: false, guardian: false, online: false };
    renderServerStatus();
  }
}

function renderServerStatus() {
  const { local, guardian, online } = S.serverStatus;
  setDot('dot-local',    local    === true  ? 'green' : local    === false ? 'red' : 'gray');
  setDot('dot-guardian', guardian === true  ? 'green' : guardian === false ? 'gray' : 'gray');
  setDot('dot-online',   online   === true  ? 'green' : online   === false ? 'gray' : 'gray');
}

function setDot(id, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `dot ${color}`;
}

// ── Mode selector ──────────────────────────────────────────────────────────────
function renderModeSelector() {
  const container = document.getElementById('mode-selector');
  if (!container) return;
  const modes = [
    { key: 'local',   label: 'Local' },
    { key: 'guarded', label: 'Guarded' },
    { key: 'hybrid',  label: 'Hybrid' },
  ];
  container.innerHTML = modes.map(m =>
    `<button class="seg-btn${S.mode === m.key ? ' active' : ''}"
       data-mode="${m.key}"
       aria-pressed="${S.mode === m.key}">${escHtml(m.label)}</button>`
  ).join('');

  container.querySelectorAll('.seg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      S.mode = btn.dataset.mode;
      renderModeSelector();
      renderModeHint();
    });
  });
  renderModeHint();
}

function renderModeHint() {
  const el = document.getElementById('mode-hint');
  if (el) el.textContent = MODE_HINTS[S.mode] ?? '';
}

// ── Category nav ───────────────────────────────────────────────────────────────
function renderCategoryNav() {
  const nav = document.getElementById('category-nav');
  if (!nav) return;

  const counts = { All: S.vault.length, Note: 0, PDF: 0, Code: 0, Other: 0 };
  S.vault.forEach(m => {
    const cat = fileCategory(m.mime_type, m.filename);
    if (counts[cat] !== undefined) counts[cat]++;
    else counts.Other++;
  });

  const categories = ['All', 'Note', 'PDF', 'Code', 'Other'];
  nav.innerHTML = categories
    .filter(c => c === 'All' || counts[c] > 0)
    .map(c =>
      `<button class="cat-btn${S.vaultFilter === c.toLowerCase() ? ' active' : ''}"
         data-cat="${c.toLowerCase()}">${escHtml(c)} ${counts[c] > 0 ? `<span style="opacity:0.6">${counts[c]}</span>` : ''}</button>`
    ).join('');

  nav.querySelectorAll('.cat-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      S.vaultFilter = btn.dataset.cat;
      renderCategoryNav();
      renderFileList();
    });
  });
}

// ── File list ──────────────────────────────────────────────────────────────────
function renderFileList() {
  const container = document.getElementById('file-list');
  if (!container) return;

  let files = S.vault;

  // Filter by category
  if (S.vaultFilter !== 'all') {
    files = files.filter(m => fileCategory(m.mime_type, m.filename).toLowerCase() === S.vaultFilter);
  }

  // Filter by search
  if (S.search.trim()) {
    const q = S.search.toLowerCase();
    files = files.filter(m => m.filename.toLowerCase().includes(q));
  }

  if (!files.length) {
    container.innerHTML = `<div class="sidebar-empty">${S.vault.length === 0 ? 'No files yet.<br>Add files to build your vault.' : 'No matches.'}</div>`;
    return;
  }

  container.innerHTML = files.map(m => {
    const cat = fileCategory(m.mime_type, m.filename);
    const icon = fileIcon(m.mime_type);
    const selected = S.selectedSources.has(m.id);
    return `<div class="file-row${selected ? ' selected' : ''}" role="listitem" data-id="${escHtml(m.id)}" title="${escHtml(m.filename)}">
      <span class="file-icon" aria-hidden="true">${icon}</span>
      <div class="file-info">
        <div class="file-name">${escHtml(m.filename)}</div>
        <div class="file-meta">${fmtSize(m.file_size)} · ${cat}</div>
      </div>
      <span class="file-check" aria-hidden="true">${selected ? '✓' : ''}</span>
    </div>`;
  }).join('');

  container.querySelectorAll('.file-row').forEach(row => {
    row.addEventListener('click', () => toggleSource(row.dataset.id, row.dataset));
  });
}

function toggleSource(id) {
  if (S.selectedSources.has(id)) {
    S.selectedSources.delete(id);
  } else {
    S.selectedSources.add(id);
  }
  renderFileList();
  renderSourceChips();
}

// ── Source chips ───────────────────────────────────────────────────────────────
function renderSourceChips() {
  const container = document.getElementById('source-chips');
  if (!container) return;

  if (S.selectedSources.size === 0) {
    container.innerHTML = '';
    return;
  }

  const chips = [...S.selectedSources].map(id => {
    const mem = S.vault.find(m => m.id === id);
    const name = mem ? mem.filename : id;
    return `<span class="source-chip">
      <span>${escHtml(name)}</span>
      <button class="source-chip-x" data-id="${escHtml(id)}" aria-label="Remove ${escHtml(name)} from selection">×</button>
    </span>`;
  }).join('');

  container.innerHTML = chips;

  container.querySelectorAll('.source-chip-x').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      S.selectedSources.delete(btn.dataset.id);
      renderFileList();
      renderSourceChips();
    });
  });
}

// ── Chat thread ────────────────────────────────────────────────────────────────
function renderChatThread() {
  const thread = document.getElementById('chat-thread');
  if (!thread) return;

  if (S.conversation.length === 0) {
    renderEmptyState(thread);
    return;
  }

  thread.innerHTML = S.conversation.map((msg, i) => renderMessage(msg, i)).join('');
  scrollToBottom();
}

function renderEmptyState(thread) {
  const hasFiles = S.vault.length > 0;

  if (hasFiles) {
    const recentFiles = S.vault.slice(0, 3);
    const chips = recentFiles.map(m =>
      `<button class="source-chip" style="cursor:pointer" data-id="${escHtml(m.id)}">${escHtml(m.filename)}</button>`
    ).join('');

    thread.innerHTML = `<div class="empty-state-wrap">
      <div class="empty-title">Ask about your knowledge</div>
      <div class="empty-sub">Select a file below or just ask — your entire vault will be searched.</div>
      <div class="source-chips" style="margin-top:8px;justify-content:center">${chips}</div>
      <div class="empty-prompts">
        <button class="prompt-chip" data-prompt="What did I write about last month?">What did I write about last month?</button>
        <button class="prompt-chip" data-prompt="Summarize my notes">Summarize my notes</button>
        <button class="prompt-chip" data-prompt="What are my key ideas?">What are my key ideas?</button>
      </div>
    </div>`;
  } else {
    thread.innerHTML = `<div class="empty-state-wrap">
      <div class="empty-title">Build your private AI vault</div>
      <div class="empty-sub">Upload documents, notes, and files to create your personal knowledge base — everything stays on your device.</div>
      <div class="empty-actions">
        <button class="btn-ghost" id="empty-upload-btn">Upload files</button>
        <button class="btn-ghost" id="empty-phone-btn">Connect phone</button>
        <button class="btn-primary" id="empty-ask-btn">Ask without files</button>
      </div>
      <div class="empty-prompts">
        <button class="prompt-chip" data-prompt="Summarize my notes">Summarize my notes</button>
        <button class="prompt-chip" data-prompt="Help plan my week">Help plan my week</button>
        <button class="prompt-chip" data-prompt="Review my resume privately">Review my resume privately</button>
      </div>
    </div>`;
  }

  // Wire empty state actions
  thread.querySelectorAll('.prompt-chip[data-prompt]').forEach(btn => {
    btn.addEventListener('click', () => {
      const textarea = document.getElementById('query-input');
      if (textarea) {
        textarea.value = btn.dataset.prompt;
        textarea.focus();
        autoResizeTextarea(textarea);
      }
    });
  });

  const emptyUpload = thread.querySelector('#empty-upload-btn');
  if (emptyUpload) emptyUpload.addEventListener('click', () => document.getElementById('file-input').click());

  const emptyPhone = thread.querySelector('#empty-phone-btn');
  if (emptyPhone) emptyPhone.addEventListener('click', showPairingModal);

  const emptyAsk = thread.querySelector('#empty-ask-btn');
  if (emptyAsk) emptyAsk.addEventListener('click', () => document.getElementById('query-input').focus());

  // Source chip click in empty state
  thread.querySelectorAll('.source-chip[data-id]').forEach(btn => {
    btn.addEventListener('click', () => toggleSource(btn.dataset.id));
  });
}

function renderMessage(msg) {
  if (msg.role === 'user') {
    return `<div class="msg-row user">
      <div class="bubble">${escHtml(msg.content)}</div>
    </div>`;
  }

  // Assistant message
  const meta = msg.meta || {};
  const badge = routingBadge(meta.routing);
  const sources = (meta.sources || []).map(s =>
    `<span class="badge badge-source">${escHtml(s)}</span>`
  ).join('');
  const warning = meta.warning
    ? `<div class="msg-warning">⚠️ ${escHtml(meta.warning)}</div>`
    : '';

  return `<div class="msg-row assistant">
    <div class="bubble">${escHtml(msg.content)}</div>
    ${(badge || sources) ? `<div class="msg-meta">${badge}${sources}</div>` : ''}
    ${warning}
  </div>`;
}

function addTypingIndicator() {
  const thread = document.getElementById('chat-thread');
  if (!thread) return;
  const el = document.createElement('div');
  el.className = 'msg-row assistant';
  el.id = 'typing-row';
  el.innerHTML = `<div class="typing-indicator">
    <div class="typing-dot"></div>
    <div class="typing-dot"></div>
    <div class="typing-dot"></div>
  </div>`;
  thread.appendChild(el);
  scrollToBottom();
}

function removeTypingIndicator() {
  document.getElementById('typing-row')?.remove();
}

function scrollToBottom() {
  const thread = document.getElementById('chat-thread');
  if (thread) thread.scrollTop = thread.scrollHeight;
}

// ── Submit query ───────────────────────────────────────────────────────────────
async function submitQuery() {
  const textarea = document.getElementById('query-input');
  const query = textarea.value.trim();
  if (!query) return;

  const btn = document.getElementById('ask-btn');
  btn.disabled = true;
  textarea.value = '';
  autoResizeTextarea(textarea);

  // Add user bubble immediately
  S.conversation.push({ role: 'user', content: query });
  renderChatThread();
  addTypingIndicator();

  try {
    const path = MODE_PATHS[S.mode] ?? '/ask';
    const body = { query };
    const data = await api.post(path, body);

    removeTypingIndicator();

    if (data.status === 'pending_approval') {
      S.pendingApproval = { query_id: data.query_id, query_text: query, preview: data.preview };
      showApprovalModal(data, query);
      // Optionally add a placeholder message
      S.conversation.push({
        role: 'assistant',
        content: 'Approval required before this query can be sent online.',
        meta: { routing: 'approval-required' },
        id: data.query_id,
      });
      renderChatThread();
    } else if (data.status === 'blocked') {
      S.conversation.push({
        role: 'assistant',
        content: data.answer ?? 'Query blocked.',
        meta: { routing: 'blocked', sources: data.sources || [], warning: data.warning },
      });
      renderChatThread();
    } else {
      S.lastMeta = data;
      S.conversation.push({
        role: 'assistant',
        content: data.answer ?? '',
        meta: {
          routing: data.routing,
          sources: data.sources || [],
          privacy_level: data.privacy_level,
          warning: data.warning,
        },
      });
      renderChatThread();
      if (S.contextPanelOpen) renderContextPanel();
    }
  } catch (e) {
    removeTypingIndicator();
    toast('Request failed — is the server running?', 'error');
  } finally {
    btn.disabled = false;
  }
}

// ── Approval modal ─────────────────────────────────────────────────────────────
function showApprovalModal(data) {
  const modal = document.getElementById('approval-modal');
  const content = document.getElementById('approval-content');
  if (!modal || !content) return;

  const preview = data.preview || {};
  const sendSections = [];
  const holdSections = [];

  if (preview.sanitized_context) {
    sendSections.push(`<div class="modal-section">
      <div class="modal-section-label">Sanitized context to be sent</div>
      <pre class="modal-preview">${escHtml(typeof preview.sanitized_context === 'string' ? preview.sanitized_context : JSON.stringify(preview.sanitized_context, null, 2))}</pre>
    </div>`);
  }
  if (preview.will_not_send) {
    holdSections.push(`<div class="modal-section">
      <div class="modal-section-label">Held back (private data)</div>
      <pre class="modal-preview">${escHtml(typeof preview.will_not_send === 'string' ? preview.will_not_send : JSON.stringify(preview.will_not_send, null, 2))}</pre>
    </div>`);
  }

  content.innerHTML = (sendSections.join('') || '') + (holdSections.join('') || '');
  modal.style.display = 'flex';
}

function hideApprovalModal() {
  const modal = document.getElementById('approval-modal');
  if (modal) modal.style.display = 'none';
}

async function approveQuery() {
  if (!S.pendingApproval) return;
  const btn = document.getElementById('approval-confirm');
  if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }

  try {
    const data = await api.post(`/ask/${S.pendingApproval.query_id}/approve`, {
      query: S.pendingApproval.query_text,
    });
    hideApprovalModal();
    // Replace the placeholder
    const idx = S.conversation.findIndex(m => m.id === S.pendingApproval.query_id);
    const result = {
      role: 'assistant',
      content: data.answer ?? '',
      meta: {
        routing: data.routing,
        sources: data.sources || [],
        privacy_level: data.privacy_level,
        warning: data.warning,
      },
    };
    if (idx >= 0) S.conversation[idx] = result;
    else S.conversation.push(result);
    S.lastMeta = data;
    S.pendingApproval = null;
    renderChatThread();
  } catch (e) {
    toast('Approval request failed', 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Approve & continue'; }
  }
}

async function useLocalOnly() {
  if (!S.pendingApproval) return;
  hideApprovalModal();
  const btn = document.getElementById('ask-btn');
  if (btn) btn.disabled = true;
  addTypingIndicator();
  try {
    const data = await api.post('/ask/local', { query: S.pendingApproval.query_text });
    removeTypingIndicator();
    S.lastMeta = data;
    const idx = S.conversation.findIndex(m => m.id === S.pendingApproval?.query_id);
    const result = {
      role: 'assistant',
      content: data.answer ?? '',
      meta: { routing: data.routing, sources: data.sources || [], warning: data.warning },
    };
    if (idx >= 0) S.conversation[idx] = result;
    else S.conversation.push(result);
    S.pendingApproval = null;
    renderChatThread();
  } catch (e) {
    removeTypingIndicator();
    toast('Local query failed', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Context Panel ──────────────────────────────────────────────────────────────
function renderContextPanel() {
  const content = document.getElementById('panel-content');
  if (!content) return;

  if (!S.lastMeta) {
    content.innerHTML = `<p style="color:var(--text2);font-size:14px;padding:8px 0">Ask a question to see routing details here.</p>`;
    return;
  }

  const m = S.lastMeta;
  const sourceBadges = (m.sources || []).map(s =>
    `<span class="badge badge-source">${escHtml(s)}</span>`
  ).join('');

  const vaultStats = S.vault.length > 0
    ? `${S.vault.length} file${S.vault.length !== 1 ? 's' : ''} in vault`
    : 'Vault is empty';

  content.innerHTML = `
    <div class="panel-section">
      <div class="panel-label">Routing</div>
      <div class="panel-value">${routingBadge(m.routing)}</div>
    </div>
    ${m.privacy_level ? `<div class="panel-section">
      <div class="panel-label">Privacy level</div>
      <div class="panel-value" style="font-size:13px;color:var(--text2)">${escHtml(m.privacy_level.replace('_', ' '))}</div>
    </div>` : ''}
    ${m.sources?.length ? `<div class="panel-section">
      <div class="panel-label">Sources used</div>
      <div class="panel-sources">${sourceBadges}</div>
    </div>` : ''}
    <div class="panel-section">
      <div class="panel-label">Vault</div>
      <div class="panel-value" style="font-size:13px;color:var(--text2)">${escHtml(vaultStats)}</div>
    </div>
    ${m.warning ? `<div class="panel-section">
      <div class="panel-label">Warning</div>
      <div class="panel-value" style="font-size:13px;color:var(--orange)">${escHtml(m.warning)}</div>
    </div>` : ''}
  `;
}

// ── Vault ──────────────────────────────────────────────────────────────────────
async function loadVault() {
  try {
    const [status, memoriesData] = await Promise.all([
      api.get('/vault/status'),
      api.get('/vault/memories?limit=200'),
    ]);
    S.vault = memoriesData.memories ?? [];
    renderCategoryNav();
    renderFileList();
    // Re-render empty state if conversation is empty (vault count changed)
    if (S.conversation.length === 0) renderChatThread();
  } catch (_) {
    // Silently fail; server may be starting
  }
}

// ── File upload ────────────────────────────────────────────────────────────────
async function handleFileUpload(files) {
  if (!files || files.length === 0) return;
  const fileArray = Array.from(files);

  for (const file of fileArray) {
    toast(`Importing ${file.name}…`, 'info');
    const form = new FormData();
    form.append('file', file);
    try {
      await api.postForm('/vault/import', form);
      toast(`Imported: ${file.name}`, 'success');
    } catch (e) {
      if (e.status === 409) {
        toast(`Already imported: ${file.name}`, 'info');
      } else {
        toast(`Failed: ${file.name} — ${e.message || 'unknown error'}`, 'error');
      }
    }
  }

  await loadVault();
}

async function deleteMemory(id) {
  if (!confirm('Delete this memory and all its chunks?')) return;
  const ok = await api.del(`/vault/memories/${id}`);
  if (ok) {
    S.vault = S.vault.filter(m => m.id !== id);
    S.selectedSources.delete(id);
    renderCategoryNav();
    renderFileList();
    renderSourceChips();
    toast('Memory deleted');
  } else {
    toast('Delete failed', 'error');
  }
}

// ── Pairing modal ──────────────────────────────────────────────────────────────
async function showPairingModal() {
  const modal = document.getElementById('pairing-modal');
  if (!modal) return;

  document.getElementById('qr-container').innerHTML = '<div style="width:200px;height:200px;display:flex;align-items:center;justify-content:center;color:var(--text2)">Loading…</div>';
  document.getElementById('pairing-url').textContent   = '';
  document.getElementById('pairing-token').textContent = '';
  document.getElementById('pairing-timer').textContent = '';
  modal.style.display = 'flex';

  try {
    const data = await api.get('/api/pair');
    document.getElementById('qr-container').innerHTML = data.qr_svg;
    document.getElementById('pairing-url').textContent   = data.url;
    document.getElementById('pairing-token').textContent = data.token;

    const expiresAt = Date.now() + data.expires_in * 1000;
    if (S.pairing?.timer_id) clearInterval(S.pairing.timer_id);

    function updateTimer() {
      const remaining = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
      const el = document.getElementById('pairing-timer');
      if (el) el.textContent = `Expires in ${remaining}s`;
      if (remaining === 0) {
        clearInterval(S.pairing?.timer_id);
        if (el) el.textContent = 'Expired — open the panel again for a new code.';
      }
    }
    updateTimer();
    const timer_id = setInterval(updateTimer, 1000);
    S.pairing = { token: data.token, url: data.url, expires_at: expiresAt, timer_id };
  } catch (e) {
    document.getElementById('qr-container').innerHTML = '<p style="color:var(--red);font-size:13px">Failed to generate pairing code.</p>';
  }
}

function hidePairingModal() {
  const modal = document.getElementById('pairing-modal');
  if (modal) modal.style.display = 'none';
  if (S.pairing?.timer_id) clearInterval(S.pairing.timer_id);
}

// ── History (mobile tab) ───────────────────────────────────────────────────────
async function loadHistory() {
  const thread = document.getElementById('chat-thread');
  if (!thread) return;

  thread.innerHTML = '<div style="padding:24px;color:var(--text2)">Loading history…</div>';

  try {
    const [stats, queriesData] = await Promise.all([
      api.get('/queries/stats'),
      api.get('/queries?limit=50'),
    ]);

    const queries = queriesData.queries ?? [];

    const statCards = [
      { label: 'Local only',        value: stats.local_only,        color: 'var(--green)' },
      { label: 'Guarded online',    value: stats.guarded_online,    color: 'var(--blue)' },
      { label: 'Approval required', value: stats.approval_required, color: 'var(--orange)' },
      { label: 'Blocked',           value: stats.blocked,           color: 'var(--red)' },
      { label: 'Total',             value: stats.total,             color: 'var(--text)' },
    ];

    const statsHtml = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px">
      ${statCards.map(s => `<div style="background:var(--bg);border-radius:var(--radius);padding:12px 16px;min-width:80px;text-align:center">
        <div style="font-size:22px;font-weight:700;color:${s.color}">${s.value ?? 0}</div>
        <div style="font-size:11px;color:var(--text2);margin-top:2px">${escHtml(s.label)}</div>
      </div>`).join('')}
    </div>`;

    const queryRows = queries.length
      ? queries.map(q => `<div style="display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--sep)">
          ${routingBadge(q.routing_decision)}
          <span style="flex:1;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(q.query_text)}">${escHtml(q.query_text)}</span>
          <span style="font-size:12px;color:var(--text2);flex-shrink:0">${fmtDate(q.created_at)}</span>
        </div>`).join('')
      : '<div style="color:var(--text2);font-size:14px;padding:20px 0;text-align:center">No queries yet.</div>';

    thread.innerHTML = `<div style="padding:0 0 20px">
      <h2 style="font-size:18px;font-weight:600;margin-bottom:16px">Query History</h2>
      ${statsHtml}
      <div>${queryRows}</div>
    </div>`;
  } catch (e) {
    thread.innerHTML = '<div style="padding:24px;color:var(--red)">Failed to load history.</div>';
  }
}

// ── Drag & drop ────────────────────────────────────────────────────────────────
function setupDragDrop() {
  let dragDepth = 0;
  const overlay = document.getElementById('drag-overlay');

  document.addEventListener('dragenter', e => {
    e.preventDefault();
    dragDepth++;
    if (overlay) overlay.classList.add('active');
  });

  document.addEventListener('dragleave', e => {
    dragDepth--;
    if (dragDepth <= 0) {
      dragDepth = 0;
      if (overlay) overlay.classList.remove('active');
    }
  });

  document.addEventListener('dragover', e => { e.preventDefault(); });

  document.addEventListener('drop', e => {
    e.preventDefault();
    dragDepth = 0;
    if (overlay) overlay.classList.remove('active');
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) handleFileUpload(files);
  });
}

// ── Auto-resize textarea ───────────────────────────────────────────────────────
function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

function setupAutoResize() {
  const textarea = document.getElementById('query-input');
  if (!textarea) return;
  textarea.addEventListener('input', () => autoResizeTextarea(textarea));
}

// ── IME-aware Enter handling ───────────────────────────────────────────────────
// Zhuyin/Bopomofo, Pinyin, Japanese, Korean IMEs all fire compositionstart
// before the candidate window opens and compositionend after the user confirms
// a character. The browser also sets event.isComposing=true on every keydown
// that happens inside a composition session, and some legacy browsers signal
// IME activity with keyCode 229 instead of the real key code.
//
// Rule: only submit when ALL of the following are true:
//   • key === "Enter"
//   • Shift is not held (Shift+Enter → newline)
//   • isComposing flag is false
//   • keyCode is not 229 (legacy IME sentinel)
//   • our own tracked flag is false (belt-and-suspenders for browsers that
//     clear isComposing before firing keydown on the confirmation Enter)

function setupIME() {
  const textarea = document.getElementById('query-input');
  if (!textarea) return;

  textarea.addEventListener('compositionstart',  () => { S._imeActive = true;  });
  textarea.addEventListener('compositionupdate', () => { S._imeActive = true;  });
  textarea.addEventListener('compositionend',    () => { S._imeActive = false; });

  textarea.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.shiftKey) return;

    // Block submission while IME composition is active.
    // Check every available signal so no IME variant slips through.
    if (S._imeActive || e.isComposing || e.keyCode === 229) return;

    e.preventDefault();
    submitQuery();
  });
}

// ── Mobile view switching ──────────────────────────────────────────────────────
function switchMobileView(view) {
  S.mobileView = view;

  // Update tab buttons
  document.querySelectorAll('.bottom-tab').forEach(btn => {
    const active = btn.dataset.view === view;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });

  const sidebar = document.getElementById('sidebar');
  const workspace = document.getElementById('workspace');

  if (view === 'vault') {
    if (sidebar) sidebar.classList.add('mobile-open');
  } else {
    if (sidebar) sidebar.classList.remove('mobile-open');
    if (view === 'history') {
      loadHistory();
    }
  }
}

// ── Event listeners ────────────────────────────────────────────────────────────
function setupEventListeners() {
  // Ask button
  document.getElementById('ask-btn')?.addEventListener('click', submitQuery);

  // Attach button → file input
  document.getElementById('attach-btn')?.addEventListener('click', () => {
    document.getElementById('file-input').click();
  });

  // Sidebar upload button
  document.getElementById('sidebar-upload-btn')?.addEventListener('click', () => {
    document.getElementById('file-input').click();
  });

  // File input change
  document.getElementById('file-input')?.addEventListener('change', e => {
    if (e.target.files?.length) {
      handleFileUpload(e.target.files);
      e.target.value = '';
    }
  });

  // Search
  document.getElementById('vault-search')?.addEventListener('input', e => {
    S.search = e.target.value;
    renderFileList();
  });

  // Panel toggle
  document.getElementById('panel-toggle')?.addEventListener('click', () => {
    S.contextPanelOpen = !S.contextPanelOpen;
    const shell = document.getElementById('app');
    if (shell) shell.classList.toggle('panel-open', S.contextPanelOpen);
    if (S.contextPanelOpen) renderContextPanel();
  });

  // Panel close
  document.getElementById('panel-close')?.addEventListener('click', () => {
    S.contextPanelOpen = false;
    document.getElementById('app')?.classList.remove('panel-open');
  });

  // Phone button
  document.getElementById('phone-btn')?.addEventListener('click', showPairingModal);

  // Pairing modal close
  document.getElementById('pairing-close')?.addEventListener('click', hidePairingModal);

  // Approval modal
  document.getElementById('approval-cancel')?.addEventListener('click', () => {
    hideApprovalModal();
    S.pendingApproval = null;
  });
  document.getElementById('approval-local')?.addEventListener('click', useLocalOnly);
  document.getElementById('approval-confirm')?.addEventListener('click', approveQuery);

  // Close modals on backdrop click
  document.getElementById('approval-modal')?.addEventListener('click', e => {
    if (e.target === e.currentTarget) { hideApprovalModal(); S.pendingApproval = null; }
  });
  document.getElementById('pairing-modal')?.addEventListener('click', e => {
    if (e.target === e.currentTarget) hidePairingModal();
  });

  // Bottom nav
  document.querySelectorAll('.bottom-tab').forEach(btn => {
    btn.addEventListener('click', () => switchMobileView(btn.dataset.view));
  });
}

// ── Bootstrap ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
