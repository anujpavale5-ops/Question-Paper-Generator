/**
 * Question Paper Generator — Question Bank Library & Duplicate Handling
 * Vanilla JavaScript implementation (No Frameworks, No External Dependencies, No Emojis)
 */

'use strict';

const banksLibrary = (function() {
  let allBanks = [];
  let currentFilter = 'all';
  let currentSort = 'recent';
  let searchQuery = '';
  let pendingReprocessBankId = null;
  let pendingDeleteBankId = null;

  // ── DOM References ──────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  function getElements() {
    return {
      navBanks: $('nav-banks'),
      viewBanks: $('view-banks'),
      btnGotoUpload: $('btn-goto-upload'),
      statsTotalBanks: $('statsTotalBanks'),
      statsTotalQuestions: $('statsTotalQuestions'),
      statsTotalPapers: $('statsTotalPapers'),
      bankSearchInput: $('bankSearchInput'),
      bankSearchClear: $('bankSearchClear'),
      bankSortSelect: $('bankSortSelect'),
      banksGrid: $('banksGrid'),
      banksLoading: $('banksLoading'),
      banksEmptyState: $('banksEmptyState'),
      btnEmptyUpload: $('btnEmptyUpload'),
      // Duplicate Modal
      duplicateModal: $('duplicateModal'),
      dupModalTitle: $('dupModalTitle'),
      dupModalDesc: $('dupModalDesc'),
      dupBankName: $('dupBankName'),
      dupBankFilename: $('dupBankFilename'),
      dupBankQuestions: $('dupBankQuestions'),
      dupBankUnits: $('dupBankUnits'),
      dupBankPapers: $('dupBankPapers'),
      dupBankDate: $('dupBankDate'),
      btnDupOpen: $('btnDupOpen'),
      btnDupReprocess: $('btnDupReprocess'),
      btnDupSeparate: $('btnDupSeparate'),
      btnDupCancel: $('btnDupCancel'),
      btnDupCloseX: $('btnDupCloseX'),
      // Reprocess Modal
      reprocessModal: $('reprocessModal'),
      reprocessBankName: $('reprocessBankName'),
      reprocessBankQuestions: $('reprocessBankQuestions'),
      reprocessBankUnits: $('reprocessBankUnits'),
      reprocessProgress: $('reprocessProgress'),
      reprocessProgressLabel: $('reprocessProgressLabel'),
      reprocessFooter: $('reprocessFooter'),
      btnConfirmReprocess: $('btnConfirmReprocess'),
      btnCancelReprocess: $('btnCancelReprocess'),
      btnReprocessCloseX: $('btnReprocessCloseX'),
      // Delete Modal
      deleteModal: $('deleteModal'),
      deleteBankName: $('deleteBankName'),
      btnConfirmDelete: $('btnConfirmDelete'),
      btnCancelDelete: $('btnCancelDelete'),
      btnDeleteCloseX: $('btnDeleteCloseX'),
      // Toast
      toastContainer: $('appToastContainer')
    };
  }

  // ── Initialize ──────────────────────────────────────────────────────────────
  function init() {
    const els = getElements();

    if (els.navBanks) {
      els.navBanks.addEventListener('click', () => {
        if (typeof switchView === 'function') {
          switchView('banks');
        }
      });
    }

    if (els.btnGotoUpload) {
      els.btnGotoUpload.addEventListener('click', () => {
        if (typeof switchView === 'function') {
          switchView('extraction');
        }
      });
    }

    if (els.btnEmptyUpload) {
      els.btnEmptyUpload.addEventListener('click', () => {
        if (typeof switchView === 'function') {
          switchView('extraction');
        }
      });
    }

    // Search input
    if (els.bankSearchInput) {
      els.bankSearchInput.addEventListener('input', (e) => {
        searchQuery = (e.target.value || '').trim().toLowerCase();
        if (els.bankSearchClear) {
          els.bankSearchClear.classList.toggle('hidden', searchQuery.length === 0);
        }
        filterAndRenderBanks();
      });
    }

    if (els.bankSearchClear) {
      els.bankSearchClear.addEventListener('click', () => {
        if (els.bankSearchInput) {
          els.bankSearchInput.value = '';
          searchQuery = '';
          els.bankSearchClear.classList.add('hidden');
          filterAndRenderBanks();
        }
      });
    }

    // Filter tabs
    document.querySelectorAll('.bank-filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.bank-filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.filter || 'all';
        filterAndRenderBanks();
      });
    });

    // Sort select
    if (els.bankSortSelect) {
      els.bankSortSelect.addEventListener('change', (e) => {
        currentSort = e.target.value || 'recent';
        filterAndRenderBanks();
      });
    }

    // Modal Close buttons
    if (els.btnReprocessCloseX) els.btnReprocessCloseX.addEventListener('click', closeReprocessModal);
    if (els.btnCancelReprocess) els.btnCancelReprocess.addEventListener('click', closeReprocessModal);
    if (els.btnDeleteCloseX) els.btnDeleteCloseX.addEventListener('click', closeDeleteModal);
    if (els.btnCancelDelete) els.btnCancelDelete.addEventListener('click', closeDeleteModal);

    // Modal Actions
    if (els.btnConfirmReprocess) els.btnConfirmReprocess.addEventListener('click', executeReprocess);
    if (els.btnConfirmDelete) els.btnConfirmDelete.addEventListener('click', executeDelete);
  }

  // ── Load Banks ──────────────────────────────────────────────────────────────
  async function loadBanks() {
    const els = getElements();
    if (els.banksLoading) els.banksLoading.classList.remove('hidden');
    if (els.banksGrid) els.banksGrid.innerHTML = '';
    if (els.banksEmptyState) els.banksEmptyState.classList.add('hidden');

    try {
      const res = await fetch('/api/banks');
      if (!res.ok) throw new Error('Failed to fetch question banks');
      allBanks = await res.json();
      updateMetrics(allBanks);
      filterAndRenderBanks();
    } catch (err) {
      showToast('Error loading question banks: ' + err.message, 'error');
    } finally {
      if (els.banksLoading) els.banksLoading.classList.add('hidden');
    }
  }

  function updateMetrics(banks) {
    const els = getElements();
    const totalBanks = banks.length;
    let totalQuestions = 0;
    let totalPapers = 0;

    banks.forEach(b => {
      totalQuestions += (b.question_count || 0);
      totalPapers += (b.paper_count || 0);
    });

    if (els.statsTotalBanks) els.statsTotalBanks.textContent = totalBanks;
    if (els.statsTotalQuestions) els.statsTotalQuestions.textContent = totalQuestions;
    if (els.statsTotalPapers) els.statsTotalPapers.textContent = totalPapers;
  }

  // ── Filter & Sort ───────────────────────────────────────────────────────────
  function filterAndRenderBanks() {
    const els = getElements();
    if (!els.banksGrid) return;

    let filtered = [...allBanks];

    // Filter by status tab
    if (currentFilter === 'ready') {
      filtered = filtered.filter(b => b.review_status === 'ready');
    } else if (currentFilter === 'needs_review') {
      filtered = filtered.filter(b => b.review_status === 'needs_review');
    }

    // Filter by search query
    if (searchQuery) {
      filtered = filtered.filter(b => {
        const name = (b.name || '').toLowerCase();
        const fname = (b.source_filename || '').toLowerCase();
        const desc = (b.description || '').toLowerCase();
        const subj = (b.subject || '').toLowerCase();
        const units = Object.keys(b.unit_distribution || {}).join(' ').toLowerCase();
        return name.includes(searchQuery) || fname.includes(searchQuery) || 
               desc.includes(searchQuery) || subj.includes(searchQuery) || units.includes(searchQuery);
      });
    }

    // Sort
    filtered.sort((a, b) => {
      if (currentSort === 'name') {
        return (a.name || '').localeCompare(b.name || '');
      } else if (currentSort === 'questions') {
        return (b.question_count || 0) - (a.question_count || 0);
      } else if (currentSort === 'papers') {
        return (b.paper_count || 0) - (a.paper_count || 0);
      } else {
        // recent
        const dateA = new Date(a.updated_at || a.created_at || 0).getTime();
        const dateB = new Date(b.updated_at || b.created_at || 0).getTime();
        return dateB - dateA;
      }
    });

    renderBanksGrid(filtered);
  }

  // ── Render Banks Grid ───────────────────────────────────────────────────────
  function renderBanksGrid(banks) {
    const els = getElements();
    els.banksGrid.innerHTML = '';

    if (banks.length === 0) {
      if (els.banksEmptyState) {
        els.banksEmptyState.classList.remove('hidden');
        const titleEl = $('emptyStateTitle');
        const msgEl = $('emptyStateMsg');
        if (searchQuery || currentFilter !== 'all') {
          if (titleEl) titleEl.textContent = 'No Matching Question Banks';
          if (msgEl) msgEl.textContent = 'Try adjusting your search terms or filters.';
        } else {
          if (titleEl) titleEl.textContent = 'No Question Banks Found';
          if (msgEl) msgEl.textContent = 'Upload a PDF or DOCX file to extract and create your first question bank.';
        }
      }
      return;
    }

    if (els.banksEmptyState) els.banksEmptyState.classList.add('hidden');

    banks.forEach(b => {
      const card = createBankCardElement(b);
      els.banksGrid.appendChild(card);
    });
  }

  function createBankCardElement(bank) {
    const card = document.createElement('article');
    card.className = 'bank-card';
    card.id = `bank-card-${bank.id}`;

    // Status Badge
    let badgeClass = 'empty';
    let badgeText = 'Empty';
    if (bank.review_status === 'ready') {
      badgeClass = 'ready';
      badgeText = 'Ready';
    } else if (bank.review_status === 'needs_review') {
      badgeClass = 'review';
      badgeText = `Needs Review (${bank.review_needed_count || 1})`;
    }

    // File info
    const sourceFilename = bank.source_filename || 'Direct Input';
    const ext = sourceFilename.includes('.') ? sourceFilename.split('.').pop().toLowerCase() : '';
    const fileTypeBadge = ext ? `<span class="file-type-pill ${ext}">${ext.toUpperCase()}</span>` : '';

    // Units tags
    const unitsMap = bank.unit_distribution || {};
    let unitTagsHtml = '';
    const unitEntries = Object.entries(unitsMap);
    if (unitEntries.length > 0) {
      unitTagsHtml = unitEntries.map(([u, count]) => 
        `<span class="unit-tag">${escapeHtml(u)}: <strong>${count}</strong></span>`
      ).join('');
    } else {
      unitTagsHtml = '<span class="unit-tag" style="color:var(--text-muted);">No units detected</span>';
    }

    // Date formatting
    const rawDate = bank.updated_at || bank.created_at;
    const formattedDate = formatDate(rawDate);
    const shortHash = bank.file_hash ? `SHA: ${bank.file_hash.substring(0, 8)}...` : '';

    card.innerHTML = `
      <div>
        <div class="bank-card-header">
          <h3 class="bank-card-title">${escapeHtml(bank.name)}</h3>
          <span class="bank-badge ${badgeClass}">${badgeText}</span>
        </div>

        <div class="bank-card-file">
          ${fileTypeBadge}
          <span title="${escapeHtml(sourceFilename)}">${escapeHtml(sourceFilename)}</span>
          ${bank.subject ? `<span class="unit-tag" style="background:#fef3c7; color:#92400e;">${escapeHtml(bank.subject)}</span>` : ''}
        </div>

        <div class="bank-card-metrics">
          <span class="metric-pill">${bank.question_count || 0} Questions</span>
          <span class="metric-pill">${bank.unit_count || 0} Units</span>
          ${(bank.paper_count && bank.paper_count > 0) ? `<span class="metric-pill" style="background:#e0e7ff; color:#3730a3;">${bank.paper_count} Papers</span>` : ''}
        </div>

        <div class="unit-tags-row">
          ${unitTagsHtml}
        </div>
      </div>

      <div class="bank-card-footer">
        <div class="bank-card-meta-line">
          <span>Processed: ${formattedDate}</span>
          ${shortHash ? `<span class="hash-label" title="Full SHA-256: ${bank.file_hash}">${shortHash}</span>` : ''}
        </div>

        <div class="bank-card-actions">
          <button class="btn btn-primary btn-card-open btn-action-open" data-id="${bank.id}">
            Open Bank
          </button>
          ${(bank.paper_count && bank.paper_count > 0) ? `
          <button class="btn btn-outline btn-action-paper" data-id="${bank.id}" style="color:var(--cyan-700); border-color:var(--cyan-400);" title="View generated question paper">
            View Paper
          </button>` : ''}
          <button class="btn btn-outline btn-action-review" data-id="${bank.id}" title="Review questions and unit classifications">
            Review Questions
          </button>
          <button class="btn btn-outline btn-action-reprocess" data-id="${bank.id}" title="Re-extract and update questions safely">
            Reprocess
          </button>
          <button class="btn-outline-danger btn-action-delete" data-id="${bank.id}" title="Delete bank">
            Delete
          </button>
        </div>
      </div>
    `;

    // Wire action buttons
    card.querySelector('.btn-action-open').addEventListener('click', () => openBank(bank.id, false));
    const paperBtn = card.querySelector('.btn-action-paper');
    if (paperBtn) {
      paperBtn.addEventListener('click', () => openBank(bank.id, true));
    }
    card.querySelector('.btn-action-review').addEventListener('click', () => continueReview(bank.id));
    card.querySelector('.btn-action-reprocess').addEventListener('click', () => promptReprocess(bank.id));
    card.querySelector('.btn-action-delete').addEventListener('click', () => promptDelete(bank.id));

    return card;
  }

  // ── Open Bank ───────────────────────────────────────────────────────────────
  async function openBank(bankId, goToPaper = false) {
    try {
      showToast('Opening question bank...', 'info');

      // 1. Fetch bank details
      const bRes = await fetch(`/api/banks/${bankId}`);
      if (!bRes.ok) throw new Error('Failed to fetch bank');
      const bank = await bRes.json();

      // 2. Fetch questions for bank
      const qRes = await fetch(`/api/banks/${bankId}/questions`);
      if (!qRes.ok) throw new Error('Failed to fetch questions');
      const questions = await qRes.json();

      // 3. Set global state
      if (typeof state !== 'undefined') {
        state.currentBank = bank;
        state.questions = questions;
      }
      if (typeof paperEditor !== 'undefined' && paperEditor.headerValues) {
        paperEditor.headerValues = {};
      }

      const activeColName = $('active-collection-name');
      if (activeColName) activeColName.textContent = bank.name;

      // 4. Check for existing blueprints
      const bpListRes = await fetch(`/api/banks/${bankId}/blueprints`);
      let loadedBlueprint = null;
      if (bpListRes.ok) {
        const bps = await bpListRes.json();
        if (bps && bps.length > 0) {
          // Fetch full blueprint with main_questions
          const fullBpRes = await fetch(`/api/blueprints/${bps[0].id}`);
          if (fullBpRes.ok) {
            loadedBlueprint = await fullBpRes.json();
          }
        }
      }

      // If no blueprint exists, create a default 30-mark blueprint
      if (!loadedBlueprint) {
        const createBpRes = await fetch('/api/blueprints/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            bank_id: bank.id,
            name: `${bank.name} Blueprint`,
            paper_total: 30
          })
        });
        if (createBpRes.ok) {
          loadedBlueprint = await createBpRes.json();
        }
      }

      if (typeof state !== 'undefined') {
        state.currentBlueprint = loadedBlueprint;
        state.currentPaper = null;
      }

      // 5. Check if generated papers exist
      const papersRes = await fetch(`/api/banks/${bankId}/papers`);
      let existingPapers = [];
      if (papersRes.ok) {
        existingPapers = await papersRes.json();
      }

      let loadedPaper = null;
      if (existingPapers && existingPapers.length > 0) {
        // Load latest paper into state
        const pRes = await fetch(`/api/generate/${existingPapers[0].id}`);
        if (pRes.ok) {
          loadedPaper = await pRes.json();
          if (typeof state !== 'undefined') {
            state.currentPaper = loadedPaper;
          }
        }
      }

      if (goToPaper && loadedPaper) {
        if (typeof showPaperView === 'function') {
          showPaperView();
        } else if (typeof switchView === 'function') {
          switchView('paper');
        }
        if (typeof renderPaper === 'function') {
          renderPaper(loadedPaper);
        }
      } else {
        // Navigate to Blueprint View and render
        if (typeof switchView === 'function') {
          switchView('blueprint');
        }
        if (typeof renderBlueprint === 'function') {
          renderBlueprint();
        }
      }

      showToast(`Opened "${bank.name}" (${questions.length} questions loaded)`, 'success');

    } catch (err) {
      showToast('Error opening question bank: ' + err.message, 'error');
    }
  }

  // ── Continue Review ─────────────────────────────────────────────────────────
  async function continueReview(bankId) {
    try {
      showToast('Loading questions for review...', 'info');

      const bRes = await fetch(`/api/banks/${bankId}`);
      if (!bRes.ok) throw new Error('Failed to fetch bank');
      const bank = await bRes.json();

      const qRes = await fetch(`/api/banks/${bankId}/questions`);
      if (!qRes.ok) throw new Error('Failed to fetch questions');
      const questions = await qRes.json();

      if (typeof state !== 'undefined') {
        state.currentBank = bank;
        state.questions = questions;
      }

      // Build segmentation structure for review UI
      const segQuestions = questions.map(q => {
        const textBlock = (q.blocks || []).find(b => b.block_type === 'text');
        const qText = textBlock ? textBlock.text_content : (q.text || '');
        const revReasons = typeof q.review_reasons === 'string' 
          ? JSON.parse(q.review_reasons || '[]') 
          : (q.review_reasons || []);

        return {
          question_id: q.id,
          text: qText,
          unit: q.unit || '',
          difficulty: q.difficulty || '',
          confidence: q.confidence !== undefined ? q.confidence : 1.0,
          needs_review: q.review_status === 'needs_review',
          review_reasons: revReasons,
          page_number: q.source_page || 1,
          bloom_level: q.bloom_level || '',
          course_outcome: q.course_outcome || '',
          level: 1,
          associated_blocks: (q.blocks || []).filter(b => b.block_type !== 'text').map(b => b.id)
        };
      });

      const seg = {
        questions: segQuestions,
        auto_accepted_count: questions.filter(q => q.review_status !== 'needs_review').length,
        review_needed_count: questions.filter(q => q.review_status === 'needs_review').length,
        unit_distribution: bank.unit_distribution || {}
      };

      // Set up state.docResult so user can confirm and update this bank
      if (typeof state !== 'undefined') {
        state.docResult = {
          filename: bank.source_filename || bank.name,
          segmentation: seg,
          blocks: []
        };
        state.docId = bank.source_document_id || bank.id;
      }

      // Switch to extraction view and render segmentation
      if (typeof switchView === 'function') {
        switchView('extraction');
      }

      const resPanel = $('resultsPanel');
      if (resPanel) resPanel.classList.remove('hidden');

      if (typeof renderSegmentation === 'function') {
        renderSegmentation(seg);
      }

      const segPanel = $('segmentationPanel');
      if (segPanel) {
        segPanel.classList.remove('hidden');
        segPanel.scrollIntoView({ behavior: 'smooth' });
      }

      showToast(`Reviewing "${bank.name}" (${questions.length} questions)`, 'success');

    } catch (err) {
      showToast('Error loading questions for review: ' + err.message, 'error');
    }
  }

  // ── Prompt Reprocess ────────────────────────────────────────────────────────
  async function promptReprocess(bankId) {
    const bank = allBanks.find(b => b.id === bankId);
    if (!bank) return;

    pendingReprocessBankId = bankId;
    const els = getElements();

    if (els.reprocessBankName) els.reprocessBankName.textContent = bank.name;
    if (els.reprocessBankQuestions) els.reprocessBankQuestions.textContent = bank.question_count || 0;
    if (els.reprocessBankUnits) els.reprocessBankUnits.textContent = bank.unit_count || 0;

    if (els.reprocessProgress) els.reprocessProgress.classList.add('hidden');
    if (els.reprocessFooter) els.reprocessFooter.classList.remove('hidden');

    if (els.reprocessModal) els.reprocessModal.classList.remove('hidden');
  }

  function closeReprocessModal() {
    pendingReprocessBankId = null;
    const els = getElements();
    if (els.reprocessModal) els.reprocessModal.classList.add('hidden');
  }

  async function executeReprocess() {
    if (!pendingReprocessBankId) return;

    const els = getElements();
    if (els.reprocessProgress) els.reprocessProgress.classList.remove('hidden');
    if (els.reprocessFooter) els.reprocessFooter.classList.add('hidden');

    try {
      const res = await fetch(`/api/banks/${pendingReprocessBankId}/reprocess`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Reprocessing failed');

      closeReprocessModal();
      await loadBanks();

      const summary = data.summary || {};
      const diffStr = (summary.diff !== undefined) 
        ? ` (${summary.diff >= 0 ? '+' : ''}${summary.diff} questions)` 
        : '';
      showToast(`Bank reprocessed successfully! Total: ${summary.new_count || 0} questions${diffStr}`, 'success');

    } catch (err) {
      if (els.reprocessProgress) els.reprocessProgress.classList.add('hidden');
      if (els.reprocessFooter) els.reprocessFooter.classList.remove('hidden');
      showToast('Reprocessing failed: ' + err.message, 'error');
    }
  }

  // ── Prompt Delete ───────────────────────────────────────────────────────────
  function promptDelete(bankId) {
    const bank = allBanks.find(b => b.id === bankId);
    if (!bank) return;

    pendingDeleteBankId = bankId;
    const els = getElements();

    if (els.deleteBankName) els.deleteBankName.textContent = bank.name;
    if (els.deleteModal) els.deleteModal.classList.remove('hidden');
  }

  function closeDeleteModal() {
    pendingDeleteBankId = null;
    const els = getElements();
    if (els.deleteModal) els.deleteModal.classList.add('hidden');
  }

  async function executeDelete() {
    if (!pendingDeleteBankId) return;

    try {
      const res = await fetch(`/api/banks/${pendingDeleteBankId}`, {
        method: 'DELETE'
      });
      if (!res.ok) throw new Error('Failed to delete question bank');

      if (typeof state !== 'undefined' && state.currentBank && state.currentBank.id === pendingDeleteBankId) {
        state.currentBank = null;
        state.currentBlueprint = null;
        state.currentPaper = null;
        const activeColName = $('active-collection-name');
        if (activeColName) activeColName.textContent = 'None';
      }

      closeDeleteModal();
      await loadBanks();
      showToast('Question bank deleted successfully.', 'info');

    } catch (err) {
      showToast('Error deleting question bank: ' + err.message, 'error');
    }
  }

  // ── Intelligent Duplicate Handling ──────────────────────────────────────────
  async function computeSha256(file) {
    const buffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  function checkAndPromptDuplicate(file) {
    return new Promise(async (resolve) => {
      let fileHash = null;
      try {
        fileHash = await computeSha256(file);
      } catch (e) {
        console.warn('Could not compute client-side SHA-256:', e);
      }

      let checkUrl = `/api/banks/check-duplicate?filename=${encodeURIComponent(file.name)}`;
      if (fileHash) {
        checkUrl += `&hash=${encodeURIComponent(fileHash)}`;
      }

      try {
        const res = await fetch(checkUrl);
        const dupData = await res.json();

        if (!dupData.exists) {
          resolve({ action: 'proceed', fileHash: fileHash });
          return;
        }

        const bank = dupData.bank || {};
        const els = getElements();

        // Populate modal
        if (dupData.match_type === 'exact') {
          if (els.dupModalTitle) els.dupModalTitle.textContent = 'Identical Question Bank Found';
          if (els.dupModalDesc) {
            els.dupModalDesc.innerHTML = `An identical question bank for <strong>"${escapeHtml(file.name)}"</strong> was found in your library. What would you like to do?`;
          }
          if (els.btnDupOpen) {
            els.btnDupOpen.textContent = 'Open Existing Bank';
            els.btnDupOpen.className = 'btn btn-primary';
          }
          if (els.btnDupReprocess) els.btnDupReprocess.textContent = 'Reprocess Existing Bank';
          if (els.btnDupSeparate) els.btnDupSeparate.textContent = 'Import as Separate Bank';
        } else {
          // Filename match only
          if (els.dupModalTitle) els.dupModalTitle.textContent = 'Similarly Named File Detected';
          if (els.dupModalDesc) {
            els.dupModalDesc.innerHTML = `A question bank with the filename <strong>"${escapeHtml(file.name)}"</strong> exists, but this file has different content.`;
          }
          if (els.btnDupSeparate) {
            els.btnDupSeparate.textContent = 'Import as Separate Bank';
            els.btnDupSeparate.className = 'btn btn-primary';
          }
          if (els.btnDupReprocess) els.btnDupReprocess.textContent = 'Update / Reprocess Existing Bank';
          if (els.btnDupOpen) els.btnDupOpen.textContent = 'Open Existing Bank';
        }

        if (els.dupBankName) els.dupBankName.textContent = bank.name || file.name;
        if (els.dupBankFilename) els.dupBankFilename.textContent = bank.source_filename || file.name;
        if (els.dupBankQuestions) els.dupBankQuestions.textContent = bank.question_count || 0;
        if (els.dupBankUnits) els.dupBankUnits.textContent = bank.unit_count || 0;
        if (els.dupBankPapers) els.dupBankPapers.textContent = bank.paper_count || 0;
        if (els.dupBankDate) els.dupBankDate.textContent = formatDate(bank.updated_at || bank.created_at);

        // Show modal
        if (els.duplicateModal) els.duplicateModal.classList.remove('hidden');

        // Cleanup handlers helper
        function cleanup() {
          if (els.duplicateModal) els.duplicateModal.classList.add('hidden');
          els.btnDupOpen.removeEventListener('click', onOpen);
          els.btnDupReprocess.removeEventListener('click', onReprocess);
          els.btnDupSeparate.removeEventListener('click', onSeparate);
          els.btnDupCancel.removeEventListener('click', onCancel);
          els.btnDupCloseX.removeEventListener('click', onCancel);
        }

        function onOpen() {
          cleanup();
          resolve({ action: 'open', bank: bank, fileHash: fileHash });
        }

        function onReprocess() {
          cleanup();
          resolve({ action: 'reprocess', bank: bank, fileHash: fileHash });
        }

        function onSeparate() {
          cleanup();
          resolve({ action: 'separate', bank: bank, fileHash: fileHash });
        }

        function onCancel() {
          cleanup();
          resolve({ action: 'cancel', bank: bank, fileHash: fileHash });
        }

        els.btnDupOpen.addEventListener('click', onOpen);
        els.btnDupReprocess.addEventListener('click', onReprocess);
        els.btnDupSeparate.addEventListener('click', onSeparate);
        els.btnDupCancel.addEventListener('click', onCancel);
        els.btnDupCloseX.addEventListener('click', onCancel);

      } catch (err) {
        console.warn('Error during duplicate check:', err);
        resolve({ action: 'proceed', fileHash: fileHash });
      }
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────
  function formatDate(isoStr) {
    if (!isoStr) return 'Just now';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      });
    } catch {
      return isoStr;
    }
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function showToast(msg, type = 'info') {
    const els = getElements();
    if (!els.toastContainer) return;

    const toast = document.createElement('div');
    toast.className = 'toast-msg';
    if (type === 'error') toast.style.borderLeft = '4px solid var(--red-600)';
    if (type === 'success') toast.style.borderLeft = '4px solid var(--green-600)';
    if (type === 'info') toast.style.borderLeft = '4px solid var(--cyan-500)';
    toast.textContent = msg;

    els.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 300ms ease';
      setTimeout(() => toast.remove(), 350);
    }, 3500);
  }

  // ── Public API ──────────────────────────────────────────────────────────────
  return {
    init,
    loadBanks,
    openBank,
    continueReview,
    promptReprocess,
    promptDelete,
    checkAndPromptDuplicate,
    showToast
  };
})();

// Auto-initialize on DOMContentLoaded
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', banksLibrary.init);
} else {
  banksLibrary.init();
}
