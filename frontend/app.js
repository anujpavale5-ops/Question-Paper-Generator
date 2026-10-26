/**
 * Question Paper Generator — Phase 1
 * Frontend application logic (Vanilla JS, no framework)
 */

'use strict';

// ═══════════════════════════ CONFIG ══════════════════════════════
const API = {
  health:  '/api/health',
  process: '/api/documents/process',
  assets:  (docId, assetId) => `/api/documents/${docId}/assets/${assetId}`,
  banks:   '/api/banks',
  bank:    (id) => `/api/banks/${id}`,
  questions: (id) => `/api/banks/${id}/questions`,
  importQuestions: (id) => `/api/banks/${id}/questions/import`,
  question: (id) => `/api/questions/${id}`
};

const MAX_FILE_BYTES = 50 * 1024 * 1024;
const ALLOWED_TYPES  = new Set(['application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document']);
const ALLOWED_EXTS   = new Set(['pdf', 'docx']);

// ═══════════════════════════ STATE ═══════════════════════════════
let state = {
  file:       null,
  processing: false,
  docResult:  null,
  docId:      null,
  filter:     'all',
  banks:      [],
  currentBank: null,
  currentBlueprint: null,
  currentPaper: null,
  questions:  []
};

// ═══════════════════════════ DOM REFS ════════════════════════════
const $ = id => document.getElementById(id);
const q = sel => document.querySelector(sel);

const dropZone      = $('dropZone');
const fileInput     = $('fileInput');
const fileInfo      = $('fileInfo');
const fileName      = $('fileName');
const fileSize      = $('fileSize');
const fileTypeIcon  = $('fileTypeIcon');
const clearFile     = $('clearFile');
const uploadError   = $('uploadError');
const processBtn    = $('processBtn');
const processBtnText= $('processBtnText');
const processBtnSpinner = $('processBtnSpinner');
const progressArea  = $('progressArea');
const progressFill  = $('progressFill');
const progressLabel = $('progressLabel');
const resultsPanel  = $('resultsPanel');
const resultTitle   = $('resultTitle');
const summaryStats  = $('summaryStats');
const summaryWarnings = $('summaryWarnings');
const backBtn       = $('backBtn');
const toggleJsonBtn = $('toggleJsonBtn');
const jsonPanel     = $('jsonPanel');
const jsonOutput    = $('jsonOutput');
const blocksContainer = $('blocksContainer');
const healthBadge   = $('healthBadge');
const healthLabel   = healthBadge ? healthBadge.querySelector('.health-label') : null;
const segmentationPanel   = $('segmentationPanel');
const segmentationContent = $('segmentationContent');

const blockCardTemplate = $('blockCardTemplate');
const filterBtns = document.querySelectorAll('.filter-btn');

const navBanks = $('nav-banks');
const navExtraction = $('nav-extraction');
const navBlueprint = $('nav-blueprint');
const viewBanks = $('view-banks');
const viewExtraction = $('view-extraction');
const viewBlueprint = $('view-blueprint');

const activeCollectionName = $('active-collection-name');
const btnCancelImport = $('btn-cancel-import');
const btnImportQuestions = $('btn-import-questions');

// ═══════════════════════════ INIT ════════════════════════════════
checkHealth();

// ═══════════════════════════ NAVIGATION ══════════════════════════
if (navBanks) navBanks.addEventListener('click', () => switchView('banks'));
navExtraction.addEventListener('click', () => switchView('extraction'));
navBlueprint.addEventListener('click', () => switchView('blueprint'));

function switchView(view) {
  // Hide all views
  document.querySelectorAll('.view-section').forEach(v => {
    v.classList.add('hidden');
    v.classList.remove('active');
  });
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

  if (view === 'banks') {
    const vb = document.getElementById('view-banks') || viewBanks;
    const nb = document.getElementById('nav-banks') || navBanks;
    if (vb) { vb.classList.remove('hidden'); vb.classList.add('active'); }
    if (nb) nb.classList.add('active');
    if (typeof banksLibrary !== 'undefined' && banksLibrary.loadBanks) {
      banksLibrary.loadBanks();
    }
  } else if (view === 'extraction') {
    viewExtraction.classList.remove('hidden');
    viewExtraction.classList.add('active');
    navExtraction.classList.add('active');
  } else if (view === 'blueprint') {
    viewBlueprint.classList.remove('hidden');
    viewBlueprint.classList.add('active');
    navBlueprint.classList.add('active');
    if (typeof loadSampleTemplate === 'function' && (!state.currentBlueprint || !state.currentBlueprint.main_questions || state.currentBlueprint.main_questions.length === 0)) {
        loadSampleTemplate(30);
        if (typeof renderBlueprint === 'function') renderBlueprint();
    }
  } else if (view === 'paper') {
    const vp = document.getElementById('view-paper');
    const np = document.getElementById('nav-paper');
    if (vp) { vp.classList.remove('hidden'); vp.classList.add('active'); }
    if (np) np.classList.add('active');
  }
}

// ═══════════════════════════ HEALTH CHECK ════════════════════════
async function checkHealth() {
  if (!healthBadge || !healthLabel) return;
  try {
    const res  = await fetch(API.health);
    const data = await res.json();
    const cls  = data.status === 'ok' ? 'ok' : 'degraded';
    healthBadge.className = `health-badge ${cls}`;
    healthLabel.textContent = data.status === 'ok' ? 'All systems ready' : 'Degraded mode';
    healthBadge.title = Object.entries(data.dependencies)
      .map(([k, v]) => `${k}: ${v}`)
      .join('\n');
  } catch {
    healthBadge.className = 'health-badge error';
    healthLabel.textContent = 'Server unreachable';
  }
}

// ═══════════════════════════ DRAG & DROP ═════════════════════════
dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});
clearFile.addEventListener('click', clearSelectedFile);

// ═══════════════════════════ FILE SELECTION ═══════════════════════
function setFile(f) {
  clearError();
  const ext = f.name.split('.').pop().toLowerCase();
  if (!ALLOWED_EXTS.has(ext)) {
    showError(`Unsupported file type ".${ext}". Please upload a PDF or DOCX file.`);
    return;
  }
  if (f.size > MAX_FILE_BYTES) {
    showError(`File is too large (${fmtBytes(f.size)}). Maximum size is 50 MB.`);
    return;
  }
  state.file = f;
  fileTypeIcon.textContent = ext.toUpperCase();
  fileName.textContent = f.name;
  fileSize.textContent = fmtBytes(f.size);
  fileInfo.classList.remove('hidden');
  processBtn.disabled = false;
  processBtn.removeAttribute('aria-disabled');
}

function clearSelectedFile() {
  state.file = null;
  fileInput.value = '';
  fileInfo.classList.add('hidden');
  processBtn.disabled = true;
  processBtn.setAttribute('aria-disabled', 'true');
  clearError();
}

// ═══════════════════════════ PROCESS DOCUMENT ════════════════════
processBtn.addEventListener('click', processDocument);

async function processDocument() {
  if (!state.file || state.processing) return;
  state.processing = true;

  state.targetBankId = null;
  state.fileHash = null;

  try {
    if (typeof banksLibrary !== 'undefined' && banksLibrary.checkAndPromptDuplicate) {
      const dupDecision = await banksLibrary.checkAndPromptDuplicate(state.file);
      if (!dupDecision || dupDecision.action === 'cancel') {
        state.processing = false;
        return;
      }
      if (dupDecision.action === 'open') {
        state.processing = false;
        await banksLibrary.openBank(dupDecision.bank.id);
        return;
      }
      if (dupDecision.action === 'reprocess') {
        state.targetBankId = dupDecision.bank.id;
      }
      state.fileHash = dupDecision.fileHash;
    }
  } catch (e) {
    console.warn('Duplicate check error:', e);
  }

  // UI: processing state
  clearError();
  processBtn.disabled = true;
  processBtnText.textContent = 'Processing…';
  processBtnSpinner.classList.remove('hidden');
  progressArea.classList.remove('hidden');
  setProgress(15, 'Uploading document to server…');

  const formData = new FormData();
  formData.append('file', state.file);

  let currentP = 20;
  const progressTimer = setInterval(() => {
    if (currentP < 85) {
      currentP += Math.max(1, (85 - currentP) * 0.12);
      const stage = currentP < 40 ? 'Parsing document pages…' :
                    currentP < 65 ? 'Extracting text, tables and figures…' :
                    'Segmenting questions and metadata…';
      setProgress(Math.round(currentP), stage);
    }
  }, 450);

  try {
    const res = await fetch(API.process, { method: 'POST', body: formData });
    clearInterval(progressTimer);
    setProgress(92, 'Finalizing extracted questions…');

    const data = await res.json();

    if (!res.ok) {
      const err = new Error(data.error || `Server error ${res.status}`);
      err.data = data;
      throw err;
    }

    setProgress(100, 'Done!');
    await sleep(400);

    state.docResult = data;
    state.docId     = data.document_id;
    renderResults(data);
    showResults();

  } catch (err) {
    clearInterval(progressTimer);
    showError(err.message || 'An unexpected error occurred. Please try again.', err.data || null);
  } finally {
    clearInterval(progressTimer);
    state.processing = false;
    processBtn.disabled = false;
    processBtnText.textContent = 'Process Document';
    processBtnSpinner.classList.add('hidden');
    progressArea.classList.add('hidden');
    setProgress(0, '');
  }
}

// ═══════════════════════════ RESULTS RENDERING ═══════════════════

function renderResults(data) {
  resultTitle.textContent = `Extracted: ${data.filename}`;
  renderSummary(data.summary, data.source_format);
  renderBlocks(data.blocks);
  renderSegmentation(data.segmentation, data.diagnostics);
  jsonOutput.textContent = JSON.stringify(data, null, 2);
}

function renderSummary(s, fmt) {
  summaryStats.innerHTML = '';
  const chips = [
    { label: `${s.total_pages} page${s.total_pages !== 1 ? 's' : ''}`, cls: '' },
    { label: `${s.total_blocks} blocks`, cls: '' },
    { label: `${s.text_blocks} text`, cls: 'text-chip' },
    { label: `${s.table_blocks} table${s.table_blocks !== 1 ? 's' : ''}`, cls: 'table-chip' },
    { label: `${s.image_blocks} image${s.image_blocks !== 1 ? 's' : ''}`, cls: 'image-chip' },
    { label: `${s.figure_blocks} figure${s.figure_blocks !== 1 ? 's' : ''}`, cls: 'figure-chip' },
    { label: fmt.toUpperCase(), cls: '' },
  ];
  if (s.scanned_pages > 0) {
    chips.push({ label: `${s.scanned_pages} scanned`, cls: 'unknown-chip' });
  }
  chips.forEach(c => {
    const el = document.createElement('span');
    el.className = `stat-chip ${c.cls}`;
    el.textContent = c.label;
    summaryStats.appendChild(el);
  });

  if (s.warnings && s.warnings.length > 0) {
    summaryWarnings.classList.remove('hidden');
    summaryWarnings.innerHTML =
      `<strong>${s.warnings.length} warning${s.warnings.length !== 1 ? 's' : ''}:</strong>` +
      '<ul>' + s.warnings.map(w => `<li>${esc(w)}</li>`).join('') + '</ul>';
  } else {
    summaryWarnings.classList.add('hidden');
  }
}

function renderBlocks(blocks) {
  blocksContainer.innerHTML = '';
  if (!blocks || blocks.length === 0) {
    blocksContainer.innerHTML = '<p class="section-desc">No content blocks were extracted.</p>';
    return;
  }

  blocks.forEach(block => {
    if (state.filter !== 'all' && block.block_type !== state.filter) return;
    const card = buildBlockCard(block);
    blocksContainer.appendChild(card);
  });

  if (blocksContainer.children.length === 0) {
    blocksContainer.innerHTML = `<p class="section-desc">No blocks match the filter "${state.filter}".</p>`;
  }
}

function buildBlockCard(block) {
  const tmpl = blockCardTemplate.content.cloneNode(true);
  const card = tmpl.querySelector('.block-card');

  // Header elements
  const typeBadge = tmpl.querySelector('.block-type-badge');
  typeBadge.dataset.type = block.block_type;
  typeBadge.textContent  = block.block_type.toUpperCase();

  tmpl.querySelector('.page-num').textContent = block.page_number;
  tmpl.querySelector('.block-method').textContent = block.extraction_method;

  const valBadge = tmpl.querySelector('.validation-badge');
  valBadge.dataset.status = block.validation_status;
  valBadge.textContent    = block.validation_status;

  // Toggle expand/collapse
  const header  = tmpl.querySelector('.block-card-header');
  const body    = tmpl.querySelector('.block-card-body');
  const toggleBtn = tmpl.querySelector('.toggle-block');
  header.addEventListener('click', () => {
    const collapsed = body.classList.toggle('collapsed');
    toggleBtn.textContent = collapsed ? '▶' : '▼';
    toggleBtn.setAttribute('aria-expanded', String(!collapsed));
  });

  // Content
  const contentEl = tmpl.querySelector('.block-content');
  contentEl.appendChild(buildBlockContent(block));

  // Notes
  if (block.notes && block.notes.length > 0) {
    const notesEl = tmpl.querySelector('.block-notes');
    notesEl.classList.remove('hidden');
    const ul = notesEl.querySelector('.notes-list');
    block.notes.forEach(n => {
      const li = document.createElement('li');
      li.textContent = n;
      ul.appendChild(li);
    });
  }

  return tmpl;
}

function buildBlockContent(block) {
  switch (block.block_type) {
    case 'text':
      return buildTextContent(block);
    case 'table':
      return buildTableContent(block);
    case 'image':
    case 'figure':
      return buildImageContent(block);
    default:
      return buildUnknownContent(block);
  }
}

function buildTextContent(block) {
  const div = document.createElement('div');
  div.className = 'text-content';
  div.textContent = block.text || '(empty)';
  return div;
}

function buildTableContent(block) {
  const div = document.createElement('div');
  if (block.table_data && block.table_data.html) {
    div.innerHTML = block.table_data.html;
    const info = document.createElement('p');
    info.style.cssText = 'font-size:.75rem;color:var(--text-muted);margin-top:.375rem';
    const td = block.table_data;
    info.textContent = `${td.row_count} rows × ${td.column_count} columns`;
    div.appendChild(info);
  } else if (block.asset_id) {
    // Fallback image
    div.innerHTML = buildFallbackImageHtml(block);
    const warn = document.createElement('p');
    warn.className = 'alert alert-warning';
    warn.style.marginTop = '.5rem';
    warn.textContent = 'Table could not be reconstructed. Showing original image as fallback.';
    div.appendChild(warn);
  } else {
    div.textContent = 'No table data available.';
  }
  return div;
}

function buildImageContent(block) {
  const div = document.createElement('div');
  div.className = 'image-content';
  if (block.asset_id && state.docId) {
    const img = document.createElement('img');
    img.src = API.assets(state.docId, block.asset_id);
    img.alt = `${block.block_type} from page ${block.page_number}`;
    img.loading = 'lazy';
    div.appendChild(img);
    const meta = document.createElement('p');
    meta.style.cssText = 'font-size:.75rem;color:var(--text-muted)';
    const m = block.metadata || {};
    if (m.image_width) meta.textContent = `${m.image_width} × ${m.image_height} px`;
    div.appendChild(meta);
  } else {
    const p = document.createElement('p');
    p.textContent = 'Image asset not available.';
    div.appendChild(p);
  }
  return div;
}

function buildUnknownContent(block) {
  const div = document.createElement('div');
  div.className = 'alert alert-warning';
  div.textContent = (block.notes && block.notes[0]) || 'Unknown content block. Manual review required.';
  return div;
}

function buildFallbackImageHtml(block) {
  if (!block.asset_id || !state.docId) return '';
  const src = API.assets(state.docId, block.asset_id);
  return `<div class="image-content"><img src="${src}" alt="Table image fallback" loading="lazy" /></div>`;
}

// ── Filters ──────────────────────────────────────────────────────
filterBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    filterBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.filter = btn.dataset.filter;
    if (state.docResult) renderBlocks(state.docResult.blocks);
  });
});

// ── Segmentation ─────────────────────────────────────────────────
// ── Segmentation ─────────────────────────────────────────────────
function renderQuestionAssets(q) {
  if (!q.associated_blocks || q.associated_blocks.length === 0) return '';
  const blocks = (state.docResult && state.docResult.blocks) || [];
  const assoc = q.associated_blocks.map(id => blocks.find(b => b.block_id === id)).filter(Boolean);
  if (assoc.length === 0) return '';

  let html = '<div class="q-assets-preview" style="margin-top:0.75rem; display:flex; flex-direction:column; gap:0.5rem;">';
  
  for (const b of assoc) {
    const isTable = b.block_type === 'table';
    const isReconstructed = isTable && (b.extraction_method === 'ocr_table' || (b.metadata && b.metadata.is_reconstructed_table));
    const isImage = b.block_type === 'image' || b.block_type === 'figure';

    html += `<div class="asset-card card" id="asset-wrap-${b.block_id}" style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:0.6rem 0.75rem;">`;
    html += `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">`;
    
    if (isReconstructed) {
      html += `<span class="badge" style="background:#e0f7fa; color:#006064; border:1px solid #80deea; font-size:0.75rem; font-weight:600;">Reconstructed Table (Editable)</span>`;
    } else if (isTable) {
      html += `<span class="badge" style="background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd; font-size:0.75rem; font-weight:600;">Extracted Table (Editable)</span>`;
    } else {
      html += `<span class="badge" style="background:#f1f5f9; color:#475569; border:1px solid #cbd5e1; font-size:0.75rem; font-weight:600;">Figure / Diagram</span>`;
    }

    html += `<div style="display:flex; align-items:center; gap:0.5rem;">`;
    html += `<span style="font-size:0.75rem; color:#64748b;">Page ${b.page_number || q.page_number}</span>`;
    html += `<button type="button" class="btn btn-outline btn-sm btn-detach-asset" data-qid="${q.question_id}" data-blockid="${b.block_id}" style="color:#ef4444; border-color:#fca5a5; font-size:0.72rem; padding:2px 8px; border-radius:4px; cursor:pointer;" title="Detach this asset from this question">Detach</button>`;
    html += `</div>`;
    html += `</div>`;

    if (isTable && b.table_data && b.table_data.html) {
      html += `<div style="max-height:160px; overflow-x:auto; overflow-y:auto; font-size:0.8rem; background:#fff; border:1px solid #e2e8f0; border-radius:4px; padding:4px;">${b.table_data.html}</div>`;
      if (b.metadata && b.metadata.original_asset_id && state.docId) {
        html += `<div style="margin-top:4px;"><a href="/api/documents/${state.docId}/assets/${b.metadata.original_asset_id}" target="_blank" style="font-size:0.75rem; color:#0284c7; text-decoration:underline;">View Source Image Crop</a></div>`;
      }
    } else if (isImage && b.asset_id && state.docId) {
      html += `<div style="margin-top:4px; text-align:left;"><img src="/api/documents/${state.docId}/assets/${b.asset_id}" style="max-height:130px; max-width:100%; border:1px solid #cbd5e1; border-radius:4px; background:#fff; object-fit:contain;" onerror="this.style.display='none'" /></div>`;
    }

    html += `</div>`;
  }

  html += '</div>';
  return html;
}

function renderSegmentation(seg, diagnostics) {
  if (!seg || !seg.questions || seg.questions.length === 0) {
    segmentationPanel.classList.add('hidden');
    return;
  }
  segmentationPanel.classList.remove('hidden');

  const total = seg.questions.length;
  const autoAccepted = seg.auto_accepted_count || seg.questions.filter(q => !q.needs_review).length;
  const reviewNeeded = seg.review_needed_count || seg.questions.filter(q => q.needs_review).length;

  let distBadges = '';
  if (seg.unit_distribution) {
    distBadges = Object.entries(seg.unit_distribution)
      .map(([u, c]) => `<span class="badge" style="background:#e0f2fe; color:#0369a1; font-weight:600; margin-right:4px;">${esc(u)}: ${c}</span>`)
      .join(' ');
  }

  const html = [];

  // Summary Metrics Banner
  html.push(`
    <div class="summary-metrics-card card" style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:1.25rem; margin-bottom:1.5rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem;">
        <div>
          <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.25rem;">
            <h4 style="margin:0; font-size:1.1rem; color:#0f172a;">Document Extraction & Classification Summary</h4>
            
          </div>
          <div style="font-size:0.9rem; color:#475569;">
            <strong>Total Extracted:</strong> ${total} &nbsp;|&nbsp;
            <strong style="color:#059669;">Auto-Accepted (High Confidence):</strong> ${autoAccepted} &nbsp;|&nbsp;
            <strong style="color:${reviewNeeded > 0 ? '#d97706' : '#64748b'};">Needs Review:</strong> ${reviewNeeded}
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:0.5rem;">
          ${distBadges}
        </div>
      </div>
    </div>
  `);

  // Review Mode A: Exceptions Section
  const exceptions = seg.questions.filter(q => q.needs_review);
  if (exceptions.length > 0) {
    html.push(`
      <div class="review-exceptions-banner" style="background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:1rem; margin-bottom:1.5rem;">
        <h4 style="margin:0 0 0.5rem 0; color:#b45309; display:flex; align-items:center; gap:0.5rem;">
          Review Exceptions (${exceptions.length})
        </h4>
        <p style="margin:0 0 1rem 0; font-size:0.875rem; color:#92400e;">
          The following questions have low confidence, lack clear unit evidence, or have proposed AI fallback reconstructions. Please review or confirm them below.
        </p>
        <div class="exception-cards-list" style="display:flex; flex-direction:column; gap:0.75rem;">
    `);

    exceptions.forEach(q => {
      const u = q.unit || "";
      const reasons = (q.review_reasons || []).join(', ') || 'Uncertain unit or structure';
      const options = ["", "Unit 1", "Unit 2", "Unit 3", "Unit 4", "Unit 5", "Unit 6"].map(opt => {
        const selected = opt === u ? 'selected' : '';
        const text = opt === "" ? "-- Select Unit --" : opt;
        return `<option value="${opt}" ${selected}>${text}</option>`;
      }).join('');

      // Standard review card
        html.push(`
          <div class="card" style="padding:0.75rem 1rem; border-left:4px solid #f59e0b; background:#fff;" id="card-${q.question_id}">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;">
              <div style="flex:1;">
                <div style="font-weight:500; font-size:0.95rem; color:#1e293b; margin-bottom:0.25rem;">
                  ${esc(q.text)}
                </div>
                <div style="font-size:0.8rem; color:#b45309;">
                  <em>Reason: ${esc(reasons)} (Confidence: ${(q.confidence * 100).toFixed(0)}%)</em>
                </div>
                ${renderQuestionAssets(q)}
                </div>
              </div>
              <div style="display:flex; align-items:center; gap:0.5rem;">
                <select class="q-unit-select" data-qid="${q.question_id}" style="padding:4px 8px; font-size:0.85rem; border-radius:4px; border:1px solid #cbd5e1;">
                  ${options}
                </select>
                <button class="btn btn-outline btn-sm btn-discard-q" data-qid="${q.question_id}" style="color:#ef4444; border-color:#fca5a5;" title="Discard this question">Discard</button>
              </div>
            </div>
          </div>
        `);
    });

    html.push(`
        </div>
      </div>
    `);
  } else {
    html.push(`
      <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:0.75rem 1rem; margin-bottom:1.5rem; color:#166534; font-size:0.9rem;">
        <strong>All questions classified with high confidence.</strong> Units and question boundaries were automatically identified.
      </div>
    `);
  }

  // Question List Preview
  html.push(`
    <details style="margin-bottom:1.5rem;" ${exceptions.length === 0 ? 'open' : ''}>
      <summary style="font-weight:600; cursor:pointer; color:#0f172a; margin-bottom:0.5rem;">
        View All Candidate Questions (${total})
      </summary>
      <div class="question-list" style="max-height:400px; overflow-y:auto; border:1px solid #e2e8f0; border-radius:6px; padding:0.5rem;">
  `);

  seg.questions.forEach(q => {
    const levelLabel = q.level === 1 ? 'Main' : q.level === 2 ? 'Sub' : 'Sub-sub';
    let metaBadges = '';
    if (q.unit) metaBadges += `<span class="badge" style="background:#e0f2fe; color:#0369a1; font-size:0.75rem; margin-right:4px;">${esc(q.unit)}</span>`;
    if (q.difficulty) metaBadges += `<span class="badge diff-${q.difficulty.toLowerCase()}" style="font-size:0.75rem; margin-right:4px;">${esc(q.difficulty)}</span>`;
    if (q.bloom_level) metaBadges += `<span class="badge" style="background:#f3e8ff; color:#7e22ce; font-size:0.75rem; margin-right:4px;">${esc(q.bloom_level)}</span>`;
    if (q.course_outcome) metaBadges += `<span class="badge" style="background:#fef3c7; color:#b45309; font-size:0.75rem; margin-right:4px;">${esc(q.course_outcome)}</span>`;

    html.push(`
      <div class="question-item" style="padding:0.5rem; border-bottom:1px solid #f1f5f9; display:flex; justify-content:space-between; align-items:center;">
        <div style="flex:1;">
          <div style="font-size:0.9rem; color:#1e293b;">
            <span style="font-weight:600; color:#64748b; margin-right:6px;">[${levelLabel}]</span>
            ${esc(q.text.substring(0, 140))}${q.text.length > 140 ? '…' : ''}
          </div>
          <div style="font-size:0.75rem; color:#94a3b8; margin-top:2px;">
            Page ${q.page_number} · Confidence: ${(q.confidence * 100).toFixed(0)}%
            ${q.associated_blocks && q.associated_blocks.length > 0 ? ` · ${q.associated_blocks.length} asset(s)` : ''}
          </div>
          ${renderQuestionAssets(q)}
          </div>
        </div>
        <div>
          ${metaBadges}
        </div>
      </div>
    `);
  });

  html.push(`
      </div>
    </details>
  `);

  segmentationContent.innerHTML = html.join('');

  // Discard action
    segmentationContent.querySelectorAll('.btn-detach-asset').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const qid = btn.dataset.qid;
      const blockId = btn.dataset.blockid;
      const qObj = seg.questions.find(q => q.question_id === qid);
      if (qObj && qObj.associated_blocks) {
        qObj.associated_blocks = qObj.associated_blocks.filter(id => id !== blockId);
        const wraps = document.querySelectorAll(`[id="asset-wrap-${blockId}"]`);
        wraps.forEach(w => w.remove());
      }
    });
  });

  segmentationContent.querySelectorAll('.btn-discard-q').forEach(btn => {
    btn.addEventListener('click', () => {
      const qid = btn.dataset.qid;
      const card = document.getElementById(`card-${qid}`);
      if (card) card.remove();
      const idx = seg.questions.findIndex(q => q.question_id === qid);
      if (idx !== -1) {
        seg.questions.splice(idx, 1);
      }
    });
  });
}

// ── JSON toggle ──────────────────────────────────────────────────
toggleJsonBtn.addEventListener('click', () => {
  const hidden = jsonPanel.classList.toggle('hidden');
  toggleJsonBtn.textContent = hidden ? 'Show JSON' : 'Hide JSON';
});

// ── Back button ──────────────────────────────────────────────────
backBtn.addEventListener('click', () => {
  resultsPanel.classList.add('hidden');
  state.docResult = null;
  state.docId     = null;
  clearSelectedFile();
  filterBtns.forEach((b, i) => b.classList.toggle('active', i === 0));
  state.filter = 'all';
});

// ═══════════════════════════ UI HELPERS ══════════════════════════
function showResults() {
  resultsPanel.classList.remove('hidden');
  resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function showError(msg) {
  const msgEl = document.getElementById('uploadErrorMsg');
  if (msgEl) {
    msgEl.textContent = msg;
  } else {
    uploadError.textContent = msg;
  }
  uploadError.classList.remove('hidden');
}

function clearError() {
  const msgEl = document.getElementById('uploadErrorMsg');
  if (msgEl) msgEl.textContent = '';
  uploadError.classList.add('hidden');
}

function setProgress(pct, label) {
  progressFill.style.width = `${pct}%`;
  progressLabel.textContent = label;
}

function fmtBytes(n) {
  if (n < 1024)        return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ═══════════════════════════ QUESTION BANKS ══════════════════════


function renderBanks() {
  banksList.innerHTML = '';
  if (state.banks.length === 0) {
    banksList.innerHTML = '<p class="empty-state">No question banks found. Create one to get started.</p>';
    return;
  }
  
  for (const bank of state.banks) {
    const d = document.createElement('div');
    d.className = 'bank-card card';
    d.innerHTML = `
      <h3>${esc(bank.name)}</h3>
      <p class="desc">${esc(bank.description || 'No description')}</p>
      <div class="stats" style="margin-top:1rem; font-size:0.9rem; color:var(--text-secondary);">
        <span>${bank.question_count} Questions</span> | 
        <span>Created: ${new Date(bank.created_at).toLocaleDateString()}</span>
      </div>
    `;
    d.style.cursor = 'pointer';
    d.addEventListener('click', () => openBank(bank.id));
    banksList.appendChild(d);
  }
}






async function loadQuestions(bankId) {
  try {
    const res = await fetch(API.questions(bankId));
    state.questions = await res.json();
    renderQuestions();
  } catch (e) {
    console.error("Error loading questions", e);
  }
}

function renderQuestions() {
  bankQuestionsList.innerHTML = '';
  if (state.questions.length === 0) {
    bankQuestionsList.innerHTML = '<p class="empty-state">No questions in this bank yet.</p>';
    return;
  }
  
  for (const q of state.questions) {
    const textBlocks = q.blocks.filter(b => b.block_type === 'text');
    let previewContent = textBlocks.length 
      ? textBlocks.map(b => `<div style="white-space: pre-wrap; margin-bottom: 0.35rem;">${esc(b.text_content)}</div>`).join('') 
      : '<em>[No text content]</em>';

    // Render associated content tables
    const tableBlocks = (q.blocks || []).filter(b => b.block_type === 'table');
    for (const tb of tableBlocks) {
      if (tb.table_data && tb.table_data.html) {
        previewContent += `<div style="margin-top: 0.5rem; overflow-x: auto;">${tb.table_data.html}</div>`;
      }
    }

    // Render associated figures / images
    const imgBlocks = (q.blocks || []).filter(b => b.block_type === 'image' || b.block_type === 'figure');
    for (const ib of imgBlocks) {
      if (ib.image_path) {
        const fname = ib.image_path.split('/').pop().split('\\').pop();
        const srcDoc = q.source_document_id || '';
        const imgSrc = srcDoc ? `/api/documents/${srcDoc}/assets/${fname}` : ib.image_path;
        previewContent += `<div style="margin-top: 0.5rem;"><img src="${imgSrc}" style="max-height: 160px; max-width: 100%; border: 1px solid var(--border); border-radius: 4px;" onerror="this.style.display='none'" /></div>`;
      }
    }
    // Render options if available
    if (Array.isArray(q.options) && q.options.length > 0) {
      previewContent += `<ul style="margin:0.5rem 0 0 1.25rem; font-size:0.875rem; color:#475569;">` +
        q.options.map(opt => `<li>${esc(opt)}</li>`).join('') +
        `</ul>`;
    }

    // Render subquestions if available
    if (Array.isArray(q.subquestions) && q.subquestions.length > 0) {
      previewContent += `<div style="margin-top:0.5rem; padding-left:0.75rem; border-left:2px solid #e2e8f0;">` +
        q.subquestions.map(sq => `<div style="font-size:0.85rem; color:#334155; margin-bottom:0.25rem;"><strong>${esc(sq.label || '')}</strong> ${esc(sq.text || '')} ${sq.marks ? `<em style="color:#64748b;">[${sq.marks}M]</em>` : ''}</div>`).join('') +
        `</div>`;
    }
    
    const div = document.createElement('div');
    div.className = 'q-preview-card card';
    div.style.marginBottom = '1rem';
    
    let metaHtml = '<span class="badge" style="background:#f1f5f9; color:#475569; border:1px solid #e2e8f0;">Local</span> ';
    if (q.difficulty) metaHtml += `<span class="badge diff-${q.difficulty.toLowerCase()}">${esc(q.difficulty)}</span> `;
    if (q.cognitive_level) metaHtml += `<span class="badge cog">${esc(q.cognitive_level)}</span> `;
    if (q.unit) metaHtml += `<span class="badge unit">${esc(q.unit)}</span> `;
    
    div.innerHTML = `
      <div class="q-meta" style="margin-bottom:0.5rem;">
        ${metaHtml}
      </div>
      <div class="q-content">
        ${previewContent}
      </div>
      <div class="q-actions" style="margin-top:1rem;">
         <button class="btn error del-btn">Delete</button>
      </div>
    `;
    div.querySelector('.del-btn').addEventListener('click', async () => {
        if (confirm('Delete this question?')) {
            await fetch(API.question(q.id), { method: 'DELETE' });
            loadQuestions(state.currentBank.id);
        }
    });
    bankQuestionsList.appendChild(div);
  }
}

// ═══════════════════════════ IMPORT WORKFLOW ═════════════════════

btnCancelImport.addEventListener('click', () => {
    if (confirm('Cancel import?')) {
        resultsPanel.classList.add('hidden');
        clearSelectedFile();
    }
});




btnImportQuestions.addEventListener('click', async () => {
  if (!state.docResult || !state.docResult.segmentation || !state.docResult.segmentation.questions) return;
  
  const questions = state.docResult.segmentation.questions.map(q => {
      // Find user-selected unit if changed in review card
      const select = document.querySelector(`.q-unit-select[data-qid="${q.question_id}"]`) ||
                     document.querySelector(`.q-unit-select[data-qid="${q.block_id}"]`);
      const assignedUnit = (select && select.value) ? select.value : (q.unit || '');

      // Content blocks: Question text block + genuine associated visual/table blocks
      const blocks = [
          {
              block_type: 'text',
              text_content: q.text,
              table_data: null,
              image_path: null
          }
      ];

      // Attach genuine associated blocks if any
      for (const assocId of (q.associated_blocks || [])) {
          const ab = (state.docResult.blocks || []).find(b => b.block_id === assocId);
          if (ab) {
              blocks.push({
                  block_type: ab.block_type,
                  text_content: ab.text,
                  table_data: ab.table_data,
                  image_path: ab.asset_id || null
              });
          }
      }

      return {
          text: q.text,
          blocks: blocks,
          unit: assignedUnit,
          difficulty: q.difficulty || null,
          estimated_marks: q.estimated_marks || null,
          topic: q.topic || null,
          course_outcome: q.course_outcome || null,
          bloom_level: q.bloom_level || null,
          source_question_number: q.source_question_number || null,
          confidence: q.confidence || 1.0,
          review_status: q.needs_review ? (assignedUnit ? 'accepted' : 'needs_review') : 'accepted',
          review_reasons: q.review_reasons || [],
          source_document_id: state.docId,
          source_page: q.page_number,
          extraction_method: q.extraction_method || 'local',
          options: q.options || null,
          subquestions: q.subquestions || null
      };
  });
  
  const targetBankId = state.targetBankId || null;
  const fileHash = state.fileHash || (state.docResult && (state.docResult.file_hash || (state.docResult.upload_info && state.docResult.upload_info.file_hash)));

  const payload = {
      filename: state.docResult.filename || (state.docResult.upload_info && state.docResult.upload_info.original_filename),
      document_id: state.docId,
      file_hash: fileHash,
      target_bank_id: targetBankId,
      replace_existing: !!targetBankId,
      questions: questions
  };

  try {
      const res = await fetch('/api/banks/import', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
      });
      const data = await res.json();
      
      if (!res.ok) throw new Error(data.error);
      
      state.currentBank = data.bank;
      state.targetBankId = null;
      activeCollectionName.textContent = data.bank.name;

      // Check if blueprint already exists for this bank
      let bpData = null;
      try {
        const bpCheckRes = await fetch(`/api/banks/${data.bank.id}/blueprints`);
        if (bpCheckRes.ok) {
          const bps = await bpCheckRes.json();
          if (bps && bps.length > 0) {
            const bpFullRes = await fetch(`/api/blueprints/${bps[0].id}`);
            if (bpFullRes.ok) {
              bpData = await bpFullRes.json();
            }
          }
        }
      } catch (e) {}

      // If no blueprint exists, create default 30-mark template
      if (!bpData) {
        const bpRes = await fetch('/api/blueprints/', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({bank_id: data.bank.id, name: data.bank.name + ' Blueprint', paper_total: 30})
        });
        bpData = await bpRes.json();
      }
      state.currentBlueprint = bpData;
      
      switchView('blueprint');
      if (typeof renderBlueprint === 'function') {
          renderBlueprint();
      }
      if (typeof banksLibrary !== 'undefined' && banksLibrary.showToast) {
        banksLibrary.showToast(`Imported ${data.imported || questions.length} questions to "${data.bank.name}"`, 'success');
      }
  } catch (err) {
      alert('Error importing questions: ' + err.message);
  }
});




