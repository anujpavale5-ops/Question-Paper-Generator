// ════════════════════════ PAPER GENERATION & RICH EDITOR UI ════════════════════════

const $p = id => document.getElementById(id);

const navPaper         = $p('nav-paper');
const viewPaper        = $p('view-paper');
const paperStatus      = $p('paper-generation-status');
const paperWarnings    = $p('paper-warnings');
const paperContent     = $p('paper-content');
const btnGenerateAgain = $p('btn-generate-again');
const btnBackBlueprint = $p('btn-back-to-blueprint');

// ── Editor State ─────────────────────────────────────────────────────────────
const paperEditor = {
  isEditMode: false,
  hasUnsavedChanges: false,
  openSlotId: null,
  dirtySlots: new Set(),
  history: {}, // slotId -> { undo: [], redo: [] }
  activeImgWrapper: null,
  autosaveTimer: null,
  headerValues: {}
};

function escapeAttr(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function captureCurrentHeaderValues() {
  const inputs = document.querySelectorAll('#paper-content .print-input[data-header-id]');
  inputs.forEach(input => {
    const key = input.getAttribute('data-header-id');
    if (key) {
      paperEditor.headerValues[key] = input.value;
    }
  });
}

function getHeaderVal(key, defaultVal) {
  if (paperEditor.headerValues && paperEditor.headerValues[key] !== undefined) {
    return escapeAttr(paperEditor.headerValues[key]);
  }
  return escapeAttr(defaultVal);
}

function syncHeaderAttributes() {
  const inputs = document.querySelectorAll('#paper-content .print-input');
  inputs.forEach(input => {
    input.setAttribute('value', input.value);
  });
}

// ── Window-Level Drag & Drop Guard ───────────────────────────────────────────
// Prevent dropped files outside designated drop areas from navigating browser away
window.addEventListener('dragover', (e) => e.preventDefault(), false);
window.addEventListener('drop', (e) => e.preventDefault(), false);

// ── Navigation & Unsaved Changes Guard ───────────────────────────────────────
window.addEventListener('beforeunload', (e) => {
  if (paperEditor.hasUnsavedChanges) {
    e.preventDefault();
    e.returnValue = '';
    return '';
  }
});

navPaper.addEventListener('click', async () => {
  if (paperEditor.hasUnsavedChanges) {
    if (!confirm('You have unsaved changes in the current question paper. Discard and proceed?')) {
      return;
    }
    paperEditor.hasUnsavedChanges = false;
    paperEditor.dirtySlots.clear();
  }
  showPaperView();
  if (!state.currentBlueprint || !state.currentBlueprint.main_questions || state.currentBlueprint.main_questions.length === 0) {
    if (typeof loadSampleTemplate === 'function') {
      loadSampleTemplate(30);
    }
  }
  
  if (state.currentBlueprint) {
    if (!state.currentPaper) {
      await doGenerate();
    }
  } else {
    paperStatus.innerHTML = '<div style="padding:1rem; background:#fff3cd;">Please configure a blueprint first.</div>';
  }
});

function showPaperView() {
  document.querySelectorAll('.view-section').forEach(v => v.classList.add('hidden'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  viewPaper.classList.remove('hidden');
  navPaper.classList.add('active');
}

btnBackBlueprint.addEventListener('click', () => {
  if (paperEditor.hasUnsavedChanges) {
    if (!confirm('You have unsaved edits in this paper. Discard and return to Blueprint?')) {
      return;
    }
    paperEditor.hasUnsavedChanges = false;
    paperEditor.dirtySlots.clear();
  }
  switchView('blueprint');
});

if (btnGenerateAgain) {
  btnGenerateAgain.addEventListener('click', async () => {
    await handleGenerateAgain();
  });
}

async function handleGenerateAgain() {
  if (paperEditor.hasUnsavedChanges) {
    if (!confirm('You have unsaved changes. Generating a new paper will discard them. Continue?')) {
      return;
    }
    paperEditor.hasUnsavedChanges = false;
    paperEditor.dirtySlots.clear();
  }
  if (!state.currentBlueprint) {
    alert('No blueprint loaded.');
    return;
  }
  await doGenerate();
}

// Wire the blueprint's "Continue to Generate Paper" button
const _bpGenerateBtn = $p('btn-generate-paper');
if (_bpGenerateBtn) {
  _bpGenerateBtn.addEventListener('click', async () => {
    if (!state.currentBlueprint) {
      alert('No blueprint loaded.\nPlease configure a blueprint first.');
      return;
    }
    if (state.currentBlueprint.id) {
      try {
        await fetch('/api/blueprints/' + state.currentBlueprint.id, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(state.currentBlueprint)
        });
      } catch (e) {
        console.warn("Auto-save before paper generation failed", e);
      }
    }
    await doGenerate();
  });
}

// ── Generate ──────────────────────────────────────────────────────────────────
async function doGenerate() {
  showPaperView();
  paperEditor.isEditMode = false;
  paperEditor.hasUnsavedChanges = false;
  paperEditor.openSlotId = null;
  paperEditor.dirtySlots.clear();
  if (paperEditor.headerValues) {
    delete paperEditor.headerValues['header-time'];
    delete paperEditor.headerValues['header-total-questions'];
  }
  
  if (!state.currentBank || !state.currentBlueprint || !state.currentBlueprint.id) {
    paperStatus.innerHTML = '';
    paperWarnings.innerHTML = '';
    paperContent.innerHTML  = '';
    
    let paper = {
      is_valid: true,
      paper_total: state.currentBlueprint ? state.currentBlueprint.paper_total : 30,
      main_questions: JSON.parse(JSON.stringify(state.currentBlueprint ? state.currentBlueprint.main_questions || [] : []))
    };
    
    for (let mq of paper.main_questions) {
      for (let sq of (mq.subquestions || [])) {
        sq.is_filled = false;
        sq.question = null;
      }
    }
    
    state.currentPaper = paper;
    renderPaper(paper);
    return;
  }

  paperStatus.innerHTML = '<p style="color:var(--primary-color); font-size:1rem;">Generating paper…</p>';
  paperWarnings.innerHTML = '';
  paperContent.innerHTML  = '';

  let data = null;
  try {
    const res  = await fetch('/api/generate/', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        blueprint_id: state.currentBlueprint.id,
        bank_id:      state.currentBank.id,
      })
    });
    data = await res.json();
  } catch (e) {
    paperStatus.innerHTML = `
      <div style="color:var(--error-color); padding:1rem; background:#ffe6e6; border-radius:6px;">
        Network error: ${esc(e.message)}
      </div>`;
    return;
  }

  if (data && data.error) {
    const errList = (data.blueprint_errors || []).map(e => `<li>${esc(e)}</li>`).join('');
    paperStatus.innerHTML = `
      <div style="color:var(--error-color); padding:1rem; background:#ffe6e6; border-radius:6px;">
        <strong>Generation failed:</strong> ${esc(data.error)}
        ${errList ? `<ul style="margin:.5rem 0 0;padding-left:1.5rem;">${errList}</ul>` : ''}
      </div>`;
    return;
  }

  state.currentPaper = data;
  try {
    renderPaper(data);
  } catch (e) {
    paperStatus.innerHTML = `
      <div style="color:var(--error-color); padding:1rem; background:#ffe6e6; border-radius:6px;">
        <strong>Rendering error:</strong> ${esc(e.message)}
      </div>`;
  }
}

// ── Toast Notification Helper ─────────────────────────────────────────────────
function showToast(message, type = 'success') {
  let toast = document.getElementById('paper-editor-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'paper-editor-toast';
    toast.className = 'editor-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = `editor-toast show ${type}`;
  setTimeout(() => {
    toast.classList.remove('show');
  }, 3500);
}

// ── HTML Sanitizer ────────────────────────────────────────────────────────────
function sanitizeHtml(rawHtml) {
  if (!rawHtml) return '';
  const parser = new DOMParser();
  const doc = parser.parseFromString(rawHtml, 'text/html');
  
  // Remove dangerous executable or layout breaking tags
  const badTags = ['script', 'iframe', 'object', 'embed', 'link', 'style', 'meta', 'applet', 'form', 'input:not(.print-input)'];
  badTags.forEach(tag => {
    doc.querySelectorAll(tag).forEach(el => el.remove());
  });

  // Strip event handlers and unsafe URLs
  const allElements = doc.querySelectorAll('*');
  allElements.forEach(el => {
    const attrs = Array.from(el.attributes);
    for (const attr of attrs) {
      const name = attr.name.toLowerCase();
      const val = attr.value.trim().toLowerCase();
      if (name.startsWith('on') || val.startsWith('javascript:')) {
        el.removeAttribute(attr.name);
      }
    }
  });

  return doc.body.innerHTML;
}

// ── Slot Locator ──────────────────────────────────────────────────────────────
function findSlot(slotId) {
  if (!state.currentPaper || !state.currentPaper.main_questions) return null;
  for (const mq of state.currentPaper.main_questions) {
    for (let idx = 0; idx < (mq.subquestions || []).length; idx++) {
      const sq = mq.subquestions[idx];
      const sId = sq.slot_id || `${mq.label}_${sq.label}`;
      const charLabel = String.fromCharCode(65 + idx);
      const altId = `${mq.label}_${charLabel}`;
      if (sId === slotId || sq.slot_id === slotId || altId === slotId) {
        return { mq, sq };
      }
    }
  }
  return null;
}

// ── Edit Mode Actions ─────────────────────────────────────────────────────────
function toggleEditMode() {
  if (paperEditor.isEditMode && paperEditor.hasUnsavedChanges) {
    if (!confirm('You have unsaved changes. Exit edit mode and discard unsaved edits?')) {
      return;
    }
    paperEditor.hasUnsavedChanges = false;
    paperEditor.dirtySlots.clear();
  }
  paperEditor.isEditMode = !paperEditor.isEditMode;
  paperEditor.openSlotId = null;
  renderPaper(state.currentPaper);
}

function startEditingSlot(slotId) {
  if (paperEditor.openSlotId && paperEditor.openSlotId !== slotId && paperEditor.dirtySlots.has(paperEditor.openSlotId)) {
    if (!confirm('Save changes in the currently open question before switching?')) {
      paperEditor.dirtySlots.delete(paperEditor.openSlotId);
    } else {
      saveSlotEdit(paperEditor.openSlotId);
    }
  }
  paperEditor.isEditMode = true;
  paperEditor.openSlotId = slotId;
  renderPaper(state.currentPaper);
}

function cancelSlotEdit(slotId) {
  paperEditor.dirtySlots.delete(slotId);
  if (paperEditor.dirtySlots.size === 0) {
    paperEditor.hasUnsavedChanges = false;
  }
  paperEditor.openSlotId = null;
  renderPaper(state.currentPaper);
}

function markDirty(slotId) {
  paperEditor.dirtySlots.add(slotId);
  paperEditor.hasUnsavedChanges = true;
  triggerDraftAutosave(slotId);
  updateUnsavedIndicator();
}

function updateUnsavedIndicator() {
  const badge = document.getElementById('paper-unsaved-badge');
  if (badge) {
    if (paperEditor.hasUnsavedChanges) {
      badge.className = 'unsaved-badge dirty';
      badge.textContent = '● Unsaved Changes';
    } else {
      badge.className = 'unsaved-badge saved';
      badge.textContent = '✓ All Changes Saved';
    }
  }
  const saveAllBtn = document.getElementById('btn-save-all-paper');
  if (saveAllBtn) {
    saveAllBtn.disabled = !paperEditor.hasUnsavedChanges;
  }
}

// ── Draft Autosave & Recovery ─────────────────────────────────────────────────
function triggerDraftAutosave(slotId) {
  if (paperEditor.autosaveTimer) clearTimeout(paperEditor.autosaveTimer);
  paperEditor.autosaveTimer = setTimeout(() => {
    saveDraft();
  }, 1500);
}

function saveDraft() {
  if (!state.currentPaper || !state.currentPaper.id) return;
  captureCurrentHeaderValues();
  const draftData = {
    paperId: state.currentPaper.id,
    timestamp: Date.now(),
    slots: {},
    headerValues: Object.assign({}, paperEditor.headerValues)
  };
  
  if (paperEditor.openSlotId) {
    const editor = document.getElementById(`editor-${paperEditor.openSlotId}`);
    if (editor) {
      const clone = editor.cloneNode(true);
      clone.querySelectorAll('.resize-handle, .floating-img-toolbar').forEach(el => el.remove());
      clone.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
      draftData.slots[paperEditor.openSlotId] = sanitizeHtml(clone.innerHTML);
    }
  }
  
  for (const mq of (state.currentPaper.main_questions || [])) {
    for (const sq of (mq.subquestions || [])) {
      const sId = sq.slot_id || `${mq.label}_${sq.label}`;
      if (sq.is_edited && sq.question && sq.question.html) {
        if (!draftData.slots[sId]) {
          draftData.slots[sId] = sq.question.html;
        }
      }
    }
  }
  
  if (Object.keys(draftData.slots).length > 0 || Object.keys(draftData.headerValues).length > 0) {
    try {
      localStorage.setItem(`paper_draft_${state.currentPaper.id}`, JSON.stringify(draftData));
    } catch (e) {
      console.warn('Draft save error:', e);
    }
  }
}

function clearDraft(paperId) {
  if (!paperId) return;
  localStorage.removeItem(`paper_draft_${paperId}`);
}

function checkDraftRecovery(paperId) {
  if (!paperId) return null;
  const raw = localStorage.getItem(`paper_draft_${paperId}`);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function restoreDraft() {
  if (!state.currentPaper || !state.currentPaper.id) return;
  const draft = checkDraftRecovery(state.currentPaper.id);
  if (!draft) return;
  
  if (draft.headerValues) {
    paperEditor.headerValues = Object.assign({}, draft.headerValues);
  }
  
  if (draft.slots) {
    for (const [slotId, html] of Object.entries(draft.slots)) {
      const target = findSlot(slotId);
      if (target) {
        if (!target.sq.question) {
          target.sq.question = { id: 'edited-' + slotId, blocks: [] };
        }
        target.sq.question.html = html;
        target.sq.is_edited = true;
        target.sq.is_filled = true;
        paperEditor.dirtySlots.add(slotId);
      }
    }
  }
  paperEditor.hasUnsavedChanges = true;
  paperEditor.isEditMode = true;
  renderPaper(state.currentPaper);
  showToast('Draft restored from previous session. Click "Save All Changes" to persist.', 'success');
}

function discardDraft() {
  if (!state.currentPaper || !state.currentPaper.id) return;
  clearDraft(state.currentPaper.id);
  paperEditor.headerValues = {};
  renderPaper(state.currentPaper);
  showToast('Draft discarded.', 'success');
}

// ── Save & Reset Backend Integration ──────────────────────────────────────────
async function saveSlotEdit(slotId) {
  const editor = document.getElementById(`editor-${slotId}`);
  if (!editor) return;
  const target = findSlot(slotId);
  if (!target) return;
  
  // Clone to clean handles
  const clone = editor.cloneNode(true);
  clone.querySelectorAll('.resize-handle, .floating-img-toolbar').forEach(el => el.remove());
  clone.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  
  const cleanHtml = sanitizeHtml(clone.innerHTML);
  
  try {
    const res = await fetch(`/api/generate/${state.currentPaper.id}/slots/${slotId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        edited_content: {
          html: cleanHtml,
          blocks: [{ block_type: 'html', html: cleanHtml }]
        }
      })
    });
    const resData = await res.json();
    if (!res.ok) {
      showToast('Save failed: ' + (resData.error || 'Server error'), 'error');
      return;
    }
    if (!target.sq.question) {
      target.sq.question = { id: 'edited-' + slotId, blocks: [] };
    }
    target.sq.question.html = cleanHtml;
    target.sq.is_edited = true;
    target.sq.is_filled = true;
    paperEditor.dirtySlots.delete(slotId);
    if (paperEditor.dirtySlots.size === 0) {
      paperEditor.hasUnsavedChanges = false;
      clearDraft(state.currentPaper.id);
    }
    paperEditor.openSlotId = null;
    renderPaper(state.currentPaper);
    showToast('Question saved successfully!', 'success');
  } catch (e) {
    showToast('Network error while saving: ' + e.message, 'error');
  }
}

async function saveAllPaperEdits() {
  if (paperEditor.openSlotId) {
    const editor = document.getElementById(`editor-${paperEditor.openSlotId}`);
    if (editor) {
      const clone = editor.cloneNode(true);
      clone.querySelectorAll('.resize-handle, .floating-img-toolbar').forEach(el => el.remove());
      clone.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
      const cleanHtml = sanitizeHtml(clone.innerHTML);
      const target = findSlot(paperEditor.openSlotId);
      if (target) {
        if (!target.sq.question) {
          target.sq.question = { id: 'edited-' + paperEditor.openSlotId, blocks: [] };
        }
        target.sq.question.html = cleanHtml;
        target.sq.is_edited = true;
      }
    }
  }
  
  const edits = [];
  for (const mq of (state.currentPaper.main_questions || [])) {
    for (const sq of (mq.subquestions || [])) {
      if (sq.is_edited && sq.question && sq.question.html) {
        edits.push({
          slot_id: sq.slot_id,
          edited_content: {
            html: sq.question.html,
            blocks: [{ block_type: 'html', html: sq.question.html }]
          }
        });
      }
    }
  }
  
  try {
    const res = await fetch(`/api/generate/${state.currentPaper.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits })
    });
    const resData = await res.json();
    if (!res.ok) {
      showToast('Save all failed: ' + (resData.error || 'Server error'), 'error');
      return;
    }
    paperEditor.dirtySlots.clear();
    paperEditor.hasUnsavedChanges = false;
    clearDraft(state.currentPaper.id);
    paperEditor.openSlotId = null;
    renderPaper(state.currentPaper);
    showToast('All changes saved successfully to database!', 'success');
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function resetSlotToBank(slotId) {
  if (!confirm('Revert this question back to the original question bank version? Custom edits will be lost.')) {
    return;
  }
  try {
    const res = await fetch(`/api/generate/${state.currentPaper.id}/slots/${slotId}/reset`, {
      method: 'POST'
    });
    const resData = await res.json();
    if (!res.ok) {
      showToast('Reset failed: ' + (resData.error || 'Server error'), 'error');
      return;
    }
    const target = findSlot(slotId);
    if (target) {
      target.sq.is_edited = false;
      if (target.sq.question) {
        delete target.sq.question.html;
      }
    }
    paperEditor.dirtySlots.delete(slotId);
    if (paperEditor.dirtySlots.size === 0) {
      paperEditor.hasUnsavedChanges = false;
    }
    paperEditor.openSlotId = null;
    renderPaper(state.currentPaper);
    showToast('Question reset to original version.', 'success');
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

// ── History (Undo & Redo) ────────────────────────────────────────────────────
function pushHistory(slotId) {
  const editor = document.getElementById(`editor-${slotId}`);
  if (!editor) return;
  const hist = paperEditor.history[slotId] = paperEditor.history[slotId] || { undo: [], redo: [] };
  const currentHtml = editor.innerHTML;
  if (hist.undo.length === 0 || hist.undo[hist.undo.length - 1] !== currentHtml) {
    hist.undo.push(currentHtml);
    if (hist.undo.length > 50) hist.undo.shift();
    hist.redo = [];
  }
}

function execUndo(slotId) {
  const editor = document.getElementById(`editor-${slotId}`);
  const hist = paperEditor.history[slotId];
  if (!editor || !hist || hist.undo.length <= 1) return;
  const current = hist.undo.pop();
  hist.redo.push(current);
  const prev = hist.undo[hist.undo.length - 1];
  editor.innerHTML = prev;
  rebindAllImageHandles(editor, slotId);
  markDirty(slotId);
}

function execRedo(slotId) {
  const editor = document.getElementById(`editor-${slotId}`);
  const hist = paperEditor.history[slotId];
  if (!editor || !hist || hist.redo.length === 0) return;
  const next = hist.redo.pop();
  hist.undo.push(next);
  editor.innerHTML = next;
  rebindAllImageHandles(editor, slotId);
  markDirty(slotId);
}

// ── Formatting Commands ──────────────────────────────────────────────────────
function execFormat(cmd, value = null) {
  document.execCommand(cmd, false, value);
}

function formatBlock(tag) {
  document.execCommand('formatBlock', false, tag);
}

// ── Image Insertion & Interaction ─────────────────────────────────────────────
function triggerImageUpload(slotId) {
  let fileInput = document.getElementById('paper-editor-file-picker');
  if (!fileInput) {
    fileInput = document.createElement('input');
    fileInput.id = 'paper-editor-file-picker';
    fileInput.type = 'file';
    fileInput.accept = 'image/png,image/jpeg,image/webp';
    fileInput.style.display = 'none';
    document.body.appendChild(fileInput);
  }
  fileInput.onchange = async () => {
    if (fileInput.files && fileInput.files[0]) {
      await uploadAndInsertImage(fileInput.files[0], slotId);
      fileInput.value = '';
    }
  };
  fileInput.click();
}

async function uploadAndInsertImage(file, slotId) {
  if (!file) return;
  if (!state.currentPaper || !state.currentPaper.id) {
    showToast('Please generate the paper before uploading images.', 'error');
    return;
  }
  showToast('Uploading image...', 'success');
  const formData = new FormData();
  formData.append('image', file, file.name || 'screenshot.png');
  formData.append('slot_id', slotId);
  
  try {
    const res = await fetch(`/api/generate/${state.currentPaper.id}/upload-image`, {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (!res.ok) {
      showToast('Image upload failed: ' + (data.error || 'Server error'), 'error');
      return;
    }
    const editor = document.getElementById(`editor-${slotId}`);
    if (!editor) return;
    insertImageElement(editor, slotId, data.url, data.width, data.height);
    showToast('Image uploaded and inserted successfully!', 'success');
  } catch (err) {
    showToast('Network error during upload: ' + err.message, 'error');
  }
}

function insertImageElement(editor, slotId, imgUrl, naturalWidth, naturalHeight) {
  const initWidth = naturalWidth && naturalWidth < 400 ? naturalWidth : 350;
  const wrapper = document.createElement('div');
  wrapper.className = 'paper-img-wrapper align-center';
  wrapper.style.width = initWidth + 'px';
  wrapper.style.display = 'block';
  wrapper.style.margin = '8px auto';
  wrapper.style.textAlign = 'center';
  wrapper.setAttribute('data-natural-width', naturalWidth || initWidth);
  wrapper.setAttribute('data-natural-height', naturalHeight || '');
  
  wrapper.innerHTML = `
    <img src="${imgUrl}" style="width: 100%; height: auto; max-width: 100%;" />
    <div class="resize-handle handle-nw" data-handle="nw"></div>
    <div class="resize-handle handle-ne" data-handle="ne"></div>
    <div class="resize-handle handle-sw" data-handle="sw"></div>
    <div class="resize-handle handle-se" data-handle="se"></div>
  `;
  
  const sel = window.getSelection();
  if (sel && sel.rangeCount && editor.contains(sel.anchorNode)) {
    const range = sel.getRangeAt(0);
    range.collapse(false);
    range.insertNode(wrapper);
    const p = document.createElement('p');
    p.innerHTML = '<br>';
    wrapper.after(p);
  } else {
    editor.appendChild(wrapper);
    const p = document.createElement('p');
    p.innerHTML = '<br>';
    editor.appendChild(p);
  }
  
  attachImageInteraction(wrapper, slotId, editor);
  pushHistory(slotId);
  markDirty(slotId);
}

function attachImageInteraction(wrapper, slotId, editor) {
  // Ensure resize handles exist
  if (!wrapper.querySelector('.handle-nw')) {
    const nw = document.createElement('div'); nw.className = 'resize-handle handle-nw'; nw.setAttribute('data-handle', 'nw');
    const ne = document.createElement('div'); ne.className = 'resize-handle handle-ne'; ne.setAttribute('data-handle', 'ne');
    const sw = document.createElement('div'); sw.className = 'resize-handle handle-sw'; sw.setAttribute('data-handle', 'sw');
    const se = document.createElement('div'); se.className = 'resize-handle handle-se'; se.setAttribute('data-handle', 'se');
    wrapper.appendChild(nw); wrapper.appendChild(ne); wrapper.appendChild(sw); wrapper.appendChild(se);
  }
  
  wrapper.addEventListener('click', (e) => {
    e.stopPropagation();
    editor.querySelectorAll('.paper-img-wrapper.selected').forEach(w => w.classList.remove('selected'));
    removeFloatingImgToolbar();
    wrapper.classList.add('selected');
    showFloatingImgToolbar(wrapper, slotId, editor);
  });
  
  const handles = wrapper.querySelectorAll('.resize-handle');
  handles.forEach(handle => {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const type = handle.getAttribute('data-handle');
      const startX = e.clientX;
      const startWidth = wrapper.offsetWidth;
      
      function onMouseMove(moveEvent) {
        moveEvent.preventDefault();
        const deltaX = moveEvent.clientX - startX;
        let newWidth;
        if (type === 'se' || type === 'ne') {
          newWidth = startWidth + deltaX;
        } else {
          newWidth = startWidth - deltaX;
        }
        const maxWidth = editor.clientWidth - 10;
        newWidth = Math.max(60, Math.min(newWidth, maxWidth));
        wrapper.style.width = Math.round(newWidth) + 'px';
        
        const tbInput = document.getElementById('floating-img-width-input');
        if (tbInput) tbInput.value = Math.round(newWidth);
      }
      
      function onMouseUp() {
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
        pushHistory(slotId);
        markDirty(slotId);
      }
      
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    });
  });
}

function rebindAllImageHandles(editor, slotId) {
  editor.querySelectorAll('.paper-img-wrapper').forEach(w => {
    attachImageInteraction(w, slotId, editor);
  });
}

function showFloatingImgToolbar(wrapper, slotId, editor) {
  removeFloatingImgToolbar();
  const toolbar = document.createElement('div');
  toolbar.id = 'active-floating-img-toolbar';
  toolbar.className = 'floating-img-toolbar no-print';
  
  const currentWidth = parseInt(wrapper.style.width || wrapper.offsetWidth || 300, 10);
  
  toolbar.innerHTML = `
    <span style="font-size:0.75rem; color:#64748b;">Width:</span>
    <input type="number" id="floating-img-width-input" min="50" max="850" value="${currentWidth}" title="Adjust width in pixels" />
    <span style="font-size:0.75rem; color:#64748b;">px</span>
    <button type="button" id="btn-img-align-left" title="Align Left">Left</button>
    <button type="button" id="btn-img-align-center" title="Align Center">Center</button>
    <button type="button" id="btn-img-align-right" title="Align Right">Right</button>
    <button type="button" id="btn-img-reset" title="Reset to natural size">Natural</button>
    <button type="button" id="btn-img-caption" title="Add Caption">+ Caption</button>
    <button type="button" id="btn-img-delete" style="color:#e53935;" title="Remove image">Delete</button>
  `;
  
  wrapper.appendChild(toolbar);
  
  const widthInput = toolbar.querySelector('#floating-img-width-input');
  widthInput.addEventListener('input', () => {
    const val = parseInt(widthInput.value, 10);
    if (val && val >= 50 && val <= editor.clientWidth) {
      wrapper.style.width = val + 'px';
      markDirty(slotId);
    }
  });
  widthInput.addEventListener('change', () => pushHistory(slotId));
  
  toolbar.querySelector('#btn-img-align-left').addEventListener('click', (e) => {
    e.stopPropagation();
    wrapper.className = 'paper-img-wrapper align-left selected';
    wrapper.style.display = 'inline-block';
    wrapper.style.margin = '6px 15px 6px 0';
    wrapper.style.textAlign = 'left';
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  toolbar.querySelector('#btn-img-align-center').addEventListener('click', (e) => {
    e.stopPropagation();
    wrapper.className = 'paper-img-wrapper align-center selected';
    wrapper.style.display = 'block';
    wrapper.style.margin = '8px auto';
    wrapper.style.textAlign = 'center';
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  toolbar.querySelector('#btn-img-align-right').addEventListener('click', (e) => {
    e.stopPropagation();
    wrapper.className = 'paper-img-wrapper align-right selected';
    wrapper.style.display = 'block';
    wrapper.style.margin = '8px 0 8px auto';
    wrapper.style.textAlign = 'right';
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  toolbar.querySelector('#btn-img-reset').addEventListener('click', (e) => {
    e.stopPropagation();
    const img = wrapper.querySelector('img');
    const natWidth = img ? img.naturalWidth : parseInt(wrapper.getAttribute('data-natural-width'), 10);
    const targetWidth = natWidth ? Math.min(natWidth, editor.clientWidth - 10) : 350;
    wrapper.style.width = targetWidth + 'px';
    widthInput.value = targetWidth;
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  toolbar.querySelector('#btn-img-caption').addEventListener('click', (e) => {
    e.stopPropagation();
    let cap = wrapper.querySelector('.paper-img-caption');
    if (!cap) {
      cap = document.createElement('div');
      cap.className = 'paper-img-caption';
      cap.contentEditable = "true";
      cap.innerText = 'Figure: Enter caption here';
      wrapper.appendChild(cap);
      cap.focus();
    } else {
      cap.focus();
    }
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  toolbar.querySelector('#btn-img-delete').addEventListener('click', (e) => {
    e.stopPropagation();
    if (confirm('Remove this image from question?')) {
      wrapper.remove();
      pushHistory(slotId);
      markDirty(slotId);
    }
  });
}

function removeFloatingImgToolbar() {
  const tb = document.getElementById('active-floating-img-toolbar');
  if (tb) tb.remove();
}

// ── Table Insertion & Editing ─────────────────────────────────────────────────
function insertTableDialog(slotId) {
  const editor = document.getElementById(`editor-${slotId}`);
  if (!editor) return;
  const rows = prompt('Number of rows:', '2');
  if (!rows) return;
  const cols = prompt('Number of columns:', '3');
  if (!cols) return;
  const r = Math.max(1, Math.min(parseInt(rows, 10) || 2, 20));
  const c = Math.max(1, Math.min(parseInt(cols, 10) || 3, 10));
  insertTable(editor, slotId, r, c);
}

function insertTable(editor, slotId, rows, cols) {
  let tblHtml = '<div class="paper-table-wrapper"><table class="paper-table" border="1"><thead><tr>';
  for (let j = 0; j < cols; j++) {
    tblHtml += `<th>Header ${j + 1}</th>`;
  }
  tblHtml += '</tr></thead><tbody>';
  for (let i = 0; i < rows; i++) {
    tblHtml += '<tr>';
    for (let j = 0; j < cols; j++) {
      tblHtml += `<td>Cell ${i + 1}-${j + 1}</td>`;
    }
    tblHtml += '</tr>';
  }
  tblHtml += '</tbody></table></div><p><br></p>';
  document.execCommand('insertHTML', false, tblHtml);
  pushHistory(slotId);
  markDirty(slotId);
}

function getSelectedTableElements() {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return null;
  let node = sel.anchorNode;
  if (!node) return null;
  if (node.nodeType === Node.TEXT_NODE) node = node.parentNode;
  const cell = node.closest('td, th');
  const row = node.closest('tr');
  const table = node.closest('table');
  return { cell, row, table };
}

function tableAddRow(slotId) {
  const selInfo = getSelectedTableElements();
  if (!selInfo || !selInfo.table) {
    showToast('Place cursor inside a table cell to add a row.', 'error');
    return;
  }
  const { row, table } = selInfo;
  const colCount = table.rows[0] ? table.rows[0].cells.length : 2;
  const newRow = document.createElement('tr');
  for (let i = 0; i < colCount; i++) {
    const td = document.createElement('td');
    td.innerHTML = 'Data';
    newRow.appendChild(td);
  }
  if (row) {
    row.after(newRow);
  } else {
    const tbody = table.querySelector('tbody') || table;
    tbody.appendChild(newRow);
  }
  pushHistory(slotId);
  markDirty(slotId);
}

function tableDelRow(slotId) {
  const selInfo = getSelectedTableElements();
  if (!selInfo || !selInfo.row) {
    showToast('Place cursor inside a row to delete it.', 'error');
    return;
  }
  const { row, table } = selInfo;
  row.remove();
  if (table.rows.length === 0) {
    const wrapper = table.closest('.paper-table-wrapper');
    if (wrapper) wrapper.remove();
    else table.remove();
  }
  pushHistory(slotId);
  markDirty(slotId);
}

function tableAddCol(slotId) {
  const selInfo = getSelectedTableElements();
  if (!selInfo || !selInfo.table) {
    showToast('Place cursor inside a table cell to add a column.', 'error');
    return;
  }
  const { cell, table } = selInfo;
  const colIndex = cell ? cell.cellIndex : -1;
  for (let i = 0; i < table.rows.length; i++) {
    const tr = table.rows[i];
    const isHead = tr.parentNode && tr.parentNode.tagName === 'THEAD';
    const newCell = document.createElement(isHead ? 'th' : 'td');
    newCell.innerHTML = isHead ? `Header` : `Data`;
    if (colIndex >= 0 && colIndex < tr.cells.length) {
      tr.cells[colIndex].after(newCell);
    } else {
      tr.appendChild(newCell);
    }
  }
  pushHistory(slotId);
  markDirty(slotId);
}

function tableDelCol(slotId) {
  const selInfo = getSelectedTableElements();
  if (!selInfo || !selInfo.cell) {
    showToast('Place cursor inside a column to delete it.', 'error');
    return;
  }
  const { cell, table } = selInfo;
  const colIndex = cell.cellIndex;
  for (let i = 0; i < table.rows.length; i++) {
    const tr = table.rows[i];
    if (tr.cells[colIndex]) {
      tr.cells[colIndex].remove();
    }
  }
  if (table.rows[0] && table.rows[0].cells.length === 0) {
    const wrapper = table.closest('.paper-table-wrapper');
    if (wrapper) wrapper.remove();
    else table.remove();
  }
  pushHistory(slotId);
  markDirty(slotId);
}

// ── Safe Printing with Image Preloading ───────────────────────────────────────
async function safePrint() {
  if (paperEditor.hasUnsavedChanges) {
    if (confirm('You have unsaved changes. Would you like to save them before printing?')) {
      await saveAllPaperEdits();
    }
  }
  captureCurrentHeaderValues();
  syncHeaderAttributes();
  // Wait for all images in #paper-content to be fully loaded
  const imgs = Array.from(document.querySelectorAll('#paper-content img'));
  await Promise.all(imgs.map(img => {
    if (img.complete) return Promise.resolve();
    return new Promise(resolve => {
      img.onload = img.onerror = resolve;
    });
  }));
  window.print();
}

// ── Active Editor Initialization ──────────────────────────────────────────────
function initEditorForSlot(slotId) {
  const editor = document.getElementById(`editor-${slotId}`);
  if (!editor) return;
  
  // Initialize history
  paperEditor.history[slotId] = {
    undo: [editor.innerHTML],
    redo: []
  };
  
  // Rebind existing images inside this question
  rebindAllImageHandles(editor, slotId);
  
  // Input listener
  editor.addEventListener('input', () => {
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  // Paste listener: detects Ctrl+V screenshot/image files vs text
  editor.addEventListener('paste', async (e) => {
    const items = (e.clipboardData || window.clipboardData).items;
    let imageItem = null;
    if (items) {
      for (let i = 0; i < items.length; i++) {
        if (items[i].type.indexOf('image') !== -1) {
          imageItem = items[i];
          break;
        }
      }
    }
    if (imageItem) {
      e.preventDefault();
      const file = imageItem.getAsFile();
      await uploadAndInsertImage(file, slotId);
      return;
    }
    
    // Normal text paste: sanitize HTML
    e.preventDefault();
    const rawHtml = e.clipboardData.getData('text/html');
    const plainText = e.clipboardData.getData('text/plain');
    const clean = rawHtml ? sanitizeHtml(rawHtml) : esc(plainText);
    document.execCommand('insertHTML', false, clean);
    pushHistory(slotId);
    markDirty(slotId);
  });
  
  // Drag & drop listeners
  editor.addEventListener('dragover', (e) => {
    e.preventDefault();
    editor.classList.add('dragover');
  });
  editor.addEventListener('dragleave', () => {
    editor.classList.remove('dragover');
  });
  editor.addEventListener('drop', async (e) => {
    e.preventDefault();
    editor.classList.remove('dragover');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      const file = e.dataTransfer.files[0];
      if (file.type.startsWith('image/')) {
        await uploadAndInsertImage(file, slotId);
      }
    }
  });
  
  // Keyboard shortcuts: Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z, etc.
  editor.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey) {
      if (e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        execUndo(slotId);
      } else if (e.key === 'y' || (e.key === 'z' && e.shiftKey)) {
        e.preventDefault();
        execRedo(slotId);
      }
    }
  });
  
  // Click outside images to clear selection
  editor.addEventListener('click', (e) => {
    if (!e.target.closest('.paper-img-wrapper')) {
      editor.querySelectorAll('.paper-img-wrapper.selected').forEach(w => w.classList.remove('selected'));
      removeFloatingImgToolbar();
    }
  });
  
  editor.focus();
}

// ── Question Content Rendering ────────────────────────────────────────────────
function isQuestionBankTableHtml(html) {
  if (!html) return false;
  const h = html.toLowerCase();
  const hasIndex = h.includes('sr.') || h.includes('sr ') || h.includes('sr.no') || h.includes('q. no') || h.includes('sl. no');
  const hasQ = h.includes('question');
  const hasMeta = h.includes('difficulty') || h.includes('level') || h.includes('marks');
  if ((hasIndex && hasQ) || (hasQ && hasMeta)) return true;
  if (/<t[dh]>\s*\d+\.\s*<\/t[dh]>/.test(h) && (h.includes('easy') || h.includes('moderate') || h.includes('difficult'))) {
    return true;
  }
  return false;
}

function buildQuestionContent(sq) {
  if (!sq.is_filled || !sq.question) {
    return `<span style="color: var(--error-color); font-style: italic;">(No question assigned)</span>`;
  }
  // Check if this question has an edited HTML version
  if (sq.question.html) {
    return `<div class="question-body-rendered">${sq.question.html}</div>`;
  }
  
  let out = '';
  let isFirstTextBlock = true;
  for (const block of (sq.question.blocks || [])) {
    if (block.block_type === 'text') {
      let textContent = block.text_content || '';
      if (isFirstTextBlock) {
        textContent = textContent.replace(/^(?:(?:Q(?:uestion)?\.?\s*\d+(?:[.)\]:]+)?)|(?:\d+[.)\]:]+))(?:\s+|$)/i, '');
        isFirstTextBlock = false;
      }
      out += `<div style="margin-bottom: 4px; white-space: pre-wrap;">${esc(textContent)}</div>`;
    } else if (block.block_type === 'table' && block.table_data && block.table_data.html) {
      if (!isQuestionBankTableHtml(block.table_data.html)) {
        out += `<div class="paper-table-wrapper">${block.table_data.html}</div>`;
      }
    } else if ((block.block_type === 'image' || block.block_type === 'figure') && block.image_path) {
      const fname = block.image_path.split('/').pop().split('\\').pop();
      const srcDoc = sq.question.source_document_id || '';
      const imgSrc = srcDoc ? `/api/documents/${srcDoc}/assets/${fname}` : block.image_path;
      out += `
        <div class="paper-img-wrapper align-center" style="display:block; margin:8px auto; text-align:center;">
          <img src="${imgSrc}" style="max-width: 100%; height: auto;" onerror="this.style.display='none'" />
        </div>`;
    }
  }
  return out;
}

// ── Question Swapping ─────────────────────────────────────────────────────────
async function swapQuestion(mqLabel, sqLabel, unit, slotId) {
  if (!state.currentBank || !state.currentPaper) return;
  
  const excludeIds = [];
  let targetMq = null;
  let targetSq = null;
  
  for (const mq of (state.currentPaper.main_questions || [])) {
    for (let idx = 0; idx < (mq.subquestions || []).length; idx++) {
      const sq = mq.subquestions[idx];
      const sId = sq.slot_id || `${mq.label}_${sq.label}`;
      const charLabel = String.fromCharCode(65 + idx);
      if (sq.is_filled && sq.question) {
        excludeIds.push(sq.question.id);
      }
      if (slotId && (sq.slot_id === slotId || sId === slotId)) {
        targetMq = mq;
        targetSq = sq;
      } else if (!targetSq && mq.label === mqLabel && (sq.label === sqLabel || charLabel === sqLabel)) {
        targetMq = mq;
        targetSq = sq;
      }
    }
  }
  
  if (!targetSq) return;
  
  if (targetSq.is_edited) {
    if (!confirm('This question has been customized. Swapping will replace it with a new bank question and discard custom edits. Continue?')) {
      return;
    }
  }
  
  try {
    const res = await fetch('/api/generate/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bank_id: state.currentBank.id || state.currentBank,
        unit: unit,
        exclude_ids: excludeIds
      })
    });
    
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Failed to swap question.');
      return;
    }
    
    targetSq.question = data;
    targetSq.is_filled = true;
    targetSq.is_edited = false;
    
    // Also reset on backend if slot has an ID
    if (targetSq.slot_id) {
      try {
        await fetch(`/api/generate/${state.currentPaper.id}/slots/${targetSq.slot_id}/reset`, { method: 'POST' });
      } catch (err) {
        console.warn('Backend slot reset warning:', err);
      }
    }
    
    renderPaper(state.currentPaper);
    showToast('Question swapped successfully.', 'success');
  } catch (err) {
    alert('Network error while swapping question: ' + err.message);
  }
}

// ── Main Render Function ──────────────────────────────────────────────────────
function renderPaper(paper) {
  captureCurrentHeaderValues();
  const valid = !!paper.is_valid;
  const paperId = paper.id;

  // Status bar
  paperStatus.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;
                padding:0.75rem 1rem;border-radius:6px;
                background:${valid ? '#e8f5e9' : '#fff8e1'};
                border-left:4px solid ${valid ? '#4caf50' : '#ff9800'};">
      <span>${valid ? 'Paper generated successfully' : 'Generated with issues — some slots could not be filled'}</span>
      <strong>Total: ${paper.paper_total} marks</strong>
    </div>`;

  // Warnings
  if (paper.warnings && paper.warnings.length) {
    const items = paper.warnings.map(w => `<li style="margin-bottom:.25rem;">${esc(w)}</li>`).join('');
    paperWarnings.innerHTML = `
      <div style="padding:.75rem 1rem;background:#fff3cd;border-radius:6px;font-size:.9rem;">
        <strong>Warnings:</strong>
        <ul style="margin:.5rem 0 0;padding-left:1.5rem;">${items}</ul>
      </div>`;
  } else {
    paperWarnings.innerHTML = '';
  }

  // Draft Recovery Check
  let draftBannerHtml = '';
  const recoveredDraft = checkDraftRecovery(paperId);
  if (recoveredDraft && !paperEditor.hasUnsavedChanges) {
    draftBannerHtml = `
      <div class="draft-recovery-banner no-print">
        <div>
          <strong>Draft Found:</strong> An unsaved editing draft from a previous session is available for this paper.
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-sm btn-primary" onclick="restoreDraft()">Restore Draft</button>
          <button class="btn btn-sm btn-outline" onclick="discardDraft()">Discard Draft</button>
        </div>
      </div>
    `;
  }

  // Paper Controls Bar
  const controlsHtml = `
    <div class="no-print" style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 15px; padding: 12px 16px; background: #f0f7f9; border-radius: 6px; border-left: 4px solid var(--primary-color); flex-wrap: wrap; gap: 10px;">
       <div style="font-size: 1.1rem; display:flex; align-items:center; gap: 12px;">
         <strong>Total Marks: ${paper.paper_total}</strong>
         <span id="paper-unsaved-badge" class="unsaved-badge ${paperEditor.hasUnsavedChanges ? 'dirty' : 'saved'}">
           ${paperEditor.hasUnsavedChanges ? '● Unsaved Changes' : '✓ All Changes Saved'}
         </span>
       </div>
       <div style="display:flex; gap: 8px; flex-wrap: wrap;">
         <button class="btn btn-outline btn-sm" onclick="handleGenerateAgain()">Generate Again</button>
         <button class="btn ${paperEditor.isEditMode ? 'btn-primary' : 'btn-outline'} btn-sm" onclick="toggleEditMode()">
           ${paperEditor.isEditMode ? 'Exit Edit Mode' : 'Edit Paper'}
         </button>
         ${paperEditor.isEditMode ? `<button id="btn-save-all-paper" class="btn btn-primary btn-sm" onclick="saveAllPaperEdits()" ${paperEditor.hasUnsavedChanges ? '' : 'disabled'}>Save All Changes</button>` : ''}
         <button class="btn btn-outline btn-sm" onclick="switchView('blueprint')">Blueprint</button>
         <button class="btn btn-outline btn-sm" id="btn-export-docx" onclick="exportDocx()">Save as DOCX</button>
         <button class="btn btn-primary btn-sm" onclick="safePrint()">Print or Save as PDF</button>
       </div>
    </div>
  `;

  // Edit Mode Banner
  let editModeBannerHtml = '';
  if (paperEditor.isEditMode) {
    editModeBannerHtml = `
      <div class="paper-edit-mode-banner no-print">
        <div class="banner-info">
          <span>Edit Paper Mode Active</span>
          <span style="font-weight:normal; font-size:0.9rem; color:var(--text-secondary);">— Click "Edit" on any question to modify text, format, insert/resize images or tables.</span>
        </div>
        <div class="banner-actions">
          <button class="btn btn-sm btn-primary" onclick="saveAllPaperEdits()">Save All Changes</button>
          <button class="btn btn-sm btn-outline" onclick="toggleEditMode()">Exit Edit Mode</button>
        </div>
      </div>
    `;
  }

  // Paper Body (Times New Roman, standard format)
  let html = draftBannerHtml + controlsHtml + editModeBannerHtml + `<div class="paper-page-container">`;

  // Header Table
  const defaultCourseName = (state.currentBank && state.currentBank.subject) 
    ? state.currentBank.subject 
    : 'Course Name (e.g., Statistical Data Analysis Using R[Open Elective])';

  // Default duration based on total marks: 60 marks -> 2 Hrs., 30 marks -> 1 Hr.
  let defaultDuration = '1 Hr.';
  const totalMarks = Number(paper.paper_total) || 0;
  if (totalMarks >= 60) {
    defaultDuration = '2 Hrs.';
  } else if (totalMarks > 0 && totalMarks <= 35) {
    defaultDuration = '1 Hr.';
  } else {
    defaultDuration = totalMarks >= 50 ? '2 Hrs.' : '1 Hr.';
  }

  html += `
  <table class="paper-header-table">
    <tr>
      <td rowspan="2" style="border: 1px solid black; width: 15%; padding: 10px; vertical-align: middle;">
          <img src="/assets/PCCOE-Logo-24.png" alt="PCCOE Logo" style="width: 80px; height: auto;" />
      </td>
      <td style="border: 1px solid black; width: 65%; padding: 10px; line-height: 1.4;">
        <div style="margin-bottom:2px;">Pimpri Chinchwad Education Trust's</div>
        <strong style="font-size:1.2rem;">Pimpri Chinchwad College of Engineering</strong><br>
        <input type="text" class="print-input" data-header-id="header-status" value="${getHeaderVal('header-status', 'An Autonomous Institute')}" placeholder="Status / Affiliation" style="width:100%; text-align:center;" /><br>
        <input type="text" class="print-input" data-header-id="header-affiliation" value="${getHeaderVal('header-affiliation', '(Permanently affiliated to Savitribai Phule Pune University)')}" placeholder="(Affiliation Details)" style="width:100%; text-align:center;" />
      </td>
      <td style="border: 1px solid black; width: 20%; font-weight: bold;">
         SET - <input type="text" class="print-input" data-header-id="header-set" value="${getHeaderVal('header-set', 'I')}" style="font-weight:bold; width: 40px; display:inline-block;" />
      </td>
    </tr>
    <tr>
      <td style="border: 1px solid black; width: 65%; font-weight:bold; font-size:1.1rem; background-color:#eaeaea; padding: 5px;">
         <input type="text" class="print-input" data-header-id="header-exam-title" value="${getHeaderVal('header-exam-title', 'Summative Assessment')}" placeholder="Summative Assessment" style="font-weight:bold; background:transparent; width:100%; text-align:center;" />
      </td>
      <td style="border: 1px solid black; width: 20%; font-weight: bold;">
         SEM - <input type="text" class="print-input" data-header-id="header-sem" value="${getHeaderVal('header-sem', 'III')}" style="font-weight:bold; width: 50px; display:inline-block;" />
      </td>
    </tr>
  </table>
  <div style="text-align:center; line-height: 1.3; margin-bottom:8px; margin-top:4px; font-weight:bold;">
      <div style="font-size:1.1rem; width:100%;"><input type="text" class="print-input" data-header-id="header-programme" value="${getHeaderVal('header-programme', 'Programme (e.g., Second Year B. Tech. (Computer, IT & AIML))')}" placeholder="Programme (e.g., Second Year B. Tech. (Computer, IT & AIML))" style="font-weight:bold; width:100%; text-align:center;" /></div>
      <div style="font-size:1.1rem; width:100%;"><input type="text" class="print-input" data-header-id="header-course-name" value="${getHeaderVal('header-course-name', defaultCourseName)}" placeholder="Course Name (e.g., Statistical Data Analysis Using R[Open Elective])" style="font-weight:bold; width:100%; text-align:center;" /></div>
      <div style="display:flex; justify-content:center; align-items:center;">[<input type="text" class="print-input" data-header-id="header-course-code" value="${getHeaderVal('header-course-code', 'Course Code')}" placeholder="Course Code" style="font-weight:bold; width:220px; text-align:center;" />]</div>
      <div style="display:flex; justify-content:center; align-items:center;">(Regulation: <input type="text" class="print-input" data-header-id="header-regulation" value="${getHeaderVal('header-regulation', 'XXXX')}" placeholder="XXXX" style="font-weight:bold; width:140px; text-align:center;" />)</div>
      <div style="display:flex; justify-content:center; align-items:center;"><input type="text" class="print-input" data-header-id="header-term" value="${getHeaderVal('header-term', 'Term (e.g., Odd Semester)')}" placeholder="Term (e.g., Odd Semester)" style="font-weight:bold; width:250px; text-align:center;" /></div>
  </div>
  
  <div style="display:flex; justify-content:space-between; font-style:italic; font-weight:bold; margin-bottom:4px;">
      <div>Total No. of Questions-<input type="text" class="print-input" data-header-id="header-total-questions" value="${getHeaderVal('header-total-questions', String(paper.main_questions ? paper.main_questions.length : 0))}" style="font-weight:bold; font-style:italic; width:30px; display:inline-block;" /></div>
      <div>Total No. of Printed Pages-<input type="text" class="print-input" data-header-id="header-total-pages" value="${getHeaderVal('header-total-pages', '1')}" style="font-weight:bold; font-style:italic; width:30px; display:inline-block;" /></div>
  </div>
  <div style="display:flex; justify-content:space-between; font-style:italic; font-weight:bold; margin-bottom:8px;">
      <div>[Time: <input type="text" class="print-input" data-header-id="header-time" value="${getHeaderVal('header-time', defaultDuration)}" placeholder="${defaultDuration}" style="font-weight:bold; font-style:italic; width:100px; display:inline-block;" />]</div>
      <div>[Max. Marks: ${paper.paper_total}]</div>
  </div>

  <div style="display:flex; justify-content:flex-end; margin-bottom:8px; padding-right:1px;">
    <table style="border-collapse: collapse; border: 1px solid black;">
      <tr>
          <td style="border: 1px solid black; padding: 2px 8px; font-weight:bold; font-style:italic;">PRN</td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
          <td style="border: 1px solid black; width:28px; height:22px;"></td>
      </tr>
    </table>
  </div>

  <div style="margin-bottom:8px; line-height:1.3;">
      <strong style="font-size: 1.05rem;">Instructions:</strong><br>
      <strong>IMP: Verify that you have received a question paper with correct course, code, branch etc.</strong><br>
      <strong>i. All questions are compulsory.</strong><br>
      <strong>ii. Assume suitable data wherever necessary.</strong><br>
      <strong>iii. Neat labelled diagrams must be drawn wherever necessary.</strong><br>
      <strong>iv. Figure to right indicates full marks.</strong><br>
      <strong>v. Use of a non-programmable calculator is allowed.</strong>
  </div>
  
  <table class="paper-table-main">
      <colgroup>
          <col class="col-qno" />
          <col class="col-sub" />
          <col class="col-text" />
          <col class="col-swap no-print" />
          <col class="col-marks" />
      </colgroup>
      <thead>
          <tr>
              <th class="col-qno"></th>
              <th class="col-sub"></th>
              <th class="col-text"></th>
              <th class="col-swap no-print"></th>
              <th class="col-marks">Marks</th>
          </tr>
      </thead>
      <tbody>
  `;

  for (const mq of (paper.main_questions || [])) {
    let heading = (mq.instruction || '').trim();
    if (mq.attempt_rule === 'ALL') {
      if (!heading || heading.toLowerCase() === 'attempt the following' || heading.toLowerCase() === 'attempt all') {
        heading = 'Answer the following questions';
      }
    } else if (mq.attempt_rule === 'ANY_N') {
      if (!heading || heading.toLowerCase().startsWith('attempt any') || heading.toLowerCase() === 'attempt the following') {
        const count = mq.attempt_count || 2;
        heading = `Answer the following (Any ${count})`;
      }
    }

    // Main Question Header Row
    html += `
      <tr>
          <td class="col-qno">${esc(mq.label)}</td>
          <td class="col-sub"></td>
          <td class="col-text" style="font-weight: bold;">${esc(heading)}</td>
          <td class="col-swap no-print"></td>
          <td class="col-marks">${mq.attempt_rule === 'ANY_N' && mq.target_marks ? `[${parseFloat(mq.target_marks)}M]` : ''}</td>
      </tr>
    `;

    // Each Subquestion Row
    const sqList = mq.subquestions || [];
    for (let i = 0; i < sqList.length; i++) {
      const sq = sqList[i];
      let subLabel = '';
      if (mq.attempt_rule === 'ANY_N') {
        subLabel = sq.label && !/^[A-Z]$/i.test(sq.label) ? sq.label : `${i + 1}.`;
      } else {
        subLabel = sq.label && /^[A-Z]$/i.test(sq.label) ? sq.label.toUpperCase() : String.fromCharCode(65 + i);
      }
      sq.label = subLabel;
      const slotId = sq.slot_id || `${mq.label}_${subLabel}`;
      const isEditingThisSlot = paperEditor.openSlotId === slotId;
      const marksDisplay = (mq.attempt_rule === 'ALL' && sq.marks) ? `[${parseFloat(sq.marks)}M]` : '';
      
      html += `
        <tr>
            <td class="col-qno"></td>
            <td class="col-sub">${esc(subLabel)}</td>
            <td class="col-text" id="sq-cell-${slotId}">
                <div id="sq-content-${slotId}">
                    ${isEditingThisSlot ? renderEditorHtml(slotId, sq) : buildQuestionContent(sq)}
                </div>
            </td>
            <td class="col-swap no-print">
                ${!isEditingThisSlot ? `
                  <div style="display:flex; flex-direction:column; align-items:center; gap:4px;">
                    <button class="btn-swap-preview" onclick="swapQuestion('${esc(mq.label)}', '${esc(subLabel)}', '${esc(mq.unit)}', '${slotId}')">Swap</button>
                    ${paperEditor.isEditMode ? `
                      <button class="btn-edit-question" onclick="startEditingSlot('${slotId}')">Edit</button>
                      ${sq.is_edited ? `
                        <button class="btn-reset-slot" onclick="resetSlotToBank('${slotId}')" title="Reset question back to question bank version">Reset</button>
                        <span style="font-size:0.7rem; color:var(--cyan-700); font-weight:600;">(Edited)</span>
                      ` : ''}
                    ` : ''}
                  </div>
                ` : ''}
            </td>
            <td class="col-marks">${marksDisplay}</td>
        </tr>
      `;
    }
  }

  html += `</tbody></table>`;
  html += `<div style="text-align: center; font-weight: bold; margin-top: 20px;">**** End of Question Paper****</div>`;
  html += `</div>`;

  paperContent.innerHTML = html;

  // Track header input changes dynamically and sync attributes
  paperContent.querySelectorAll('.print-input[data-header-id]').forEach(input => {
    input.addEventListener('input', (e) => {
      const key = e.target.getAttribute('data-header-id');
      if (key) {
        paperEditor.headerValues[key] = e.target.value;
        input.setAttribute('value', e.target.value);
        triggerDraftAutosave();
      }
    });
  });

  // Mount active editor instance if open
  if (paperEditor.openSlotId) {
    initEditorForSlot(paperEditor.openSlotId);
  }
}

// ── Render Rich Editor HTML Component ─────────────────────────────────────────
function renderEditorHtml(slotId, sq) {
  const initialContent = sq.question && sq.question.html ? sq.question.html : buildQuestionContent(sq);
  
  return `
    <div class="paper-editor-container no-print" id="editor-container-${slotId}">
      <!-- Formatting Toolbar -->
      <div class="paper-editor-toolbar" role="toolbar" aria-label="Formatting options">
        <div class="tb-group">
          <button type="button" onclick="execUndo('${slotId}')" title="Undo (Ctrl+Z)">Undo</button>
          <button type="button" onclick="execRedo('${slotId}')" title="Redo (Ctrl+Y)">Redo</button>
        </div>
        <div class="tb-group">
          <button type="button" onclick="execFormat('bold')" title="Bold (Ctrl+B)"><b>B</b></button>
          <button type="button" onclick="execFormat('italic')" title="Italic (Ctrl+I)"><i>I</i></button>
          <button type="button" onclick="execFormat('underline')" title="Underline (Ctrl+U)"><u>U</u></button>
        </div>
        <div class="tb-group">
          <select onchange="formatBlock(this.value); this.value='';" title="Text Format">
            <option value="" disabled selected>Format</option>
            <option value="p">Paragraph</option>
            <option value="h3">Heading 3</option>
            <option value="h4">Heading 4</option>
            <option value="pre">Code / Monospace</option>
          </select>
        </div>
        <div class="tb-group">
          <button type="button" onclick="execFormat('justifyLeft')" title="Align Left">Left</button>
          <button type="button" onclick="execFormat('justifyCenter')" title="Align Center">Center</button>
          <button type="button" onclick="execFormat('justifyRight')" title="Align Right">Right</button>
        </div>
        <div class="tb-group">
          <button type="button" onclick="execFormat('insertUnorderedList')" title="Bullet List">Bullet List</button>
          <button type="button" onclick="execFormat('insertOrderedList')" title="Numbered List">Numbered List</button>
        </div>
        <div class="tb-group">
          <button type="button" onclick="insertTableDialog('${slotId}')" title="Insert Table">Table</button>
          <button type="button" onclick="tableAddRow('${slotId}')" title="Add Row below">+Row</button>
          <button type="button" onclick="tableDelRow('${slotId}')" title="Delete current Row">-Row</button>
          <button type="button" onclick="tableAddCol('${slotId}')" title="Add Column right">+Col</button>
          <button type="button" onclick="tableDelCol('${slotId}')" title="Delete current Column">-Col</button>
        </div>
        <div class="tb-group">
          <button type="button" onclick="triggerImageUpload('${slotId}')" title="Insert image from disk">Image</button>
        </div>
        <div class="tb-group">
          <button type="button" onclick="execFormat('removeFormat')" title="Clear formatting">Clear Format</button>
        </div>
      </div>
      
      <!-- Contenteditable Area -->
      <div id="editor-${slotId}" class="rich-question-editor" contenteditable="true" spellcheck="true" role="textbox" aria-multiline="true">
        ${initialContent}
      </div>
      
      <!-- Footer Actions -->
      <div class="paper-editor-footer">
        <button class="btn btn-sm btn-primary" onclick="saveSlotEdit('${slotId}')">Save Question</button>
        <button class="btn btn-sm btn-outline" onclick="cancelSlotEdit('${slotId}')">Cancel</button>
        ${sq.is_edited ? `<button class="btn-reset-slot" onclick="resetSlotToBank('${slotId}')">Reset to Bank</button>` : ''}
      </div>
    </div>
  `;
}

/**
 * Export generated paper to Word (.docx) format via backend python-docx endpoint.
 */
async function exportDocx() {
  if (!state.currentPaper || !state.currentPaper.id) {
    alert("No generated paper available to export.");
    return;
  }
  const btn = document.getElementById('btn-export-docx');
  const origText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Exporting...';
  }
  try {
    captureCurrentHeaderValues();
    const resp = await fetch(`/api/generate/${state.currentPaper.id}/export/docx`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        header_values: paperEditor.headerValues || {}
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `Export failed with status ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const courseName = (paperEditor.headerValues && paperEditor.headerValues['header-course-name']) || (state.currentBank && state.currentBank.subject) || 'Question_Paper';
    a.download = `${courseName.replace(/[^a-zA-Z0-9_\-]/g, '_')}_Paper.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    console.error('DOCX export error:', err);
    alert(`Failed to export DOCX: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText || 'Save as DOCX';
    }
  }
}

