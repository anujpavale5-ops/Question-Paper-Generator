const $bp = id => document.getElementById(id);
const bpPaperTotal = $bp('bp-paper-total');
const bpAllocatedMarks = $bp('bp-allocated-marks');
const bpMarksStatus = $bp('bp-marks-status');
const valArea = $bp('blueprint-validation-area');
const mqList = $bp('blueprint-mq-list');
const btnAddMq = $bp('btn-add-mq');
const btnLoadSample = $bp('btn-load-sample');
const btnSaveBlueprint = $bp('btn-save-blueprint');
const btnGeneratePaper = $bp('btn-generate-paper');

const UNITS = ["Unit 1", "Unit 2", "Unit 3", "Unit 4", "Unit 5"];

function renderBlueprint() {
    if (!state.currentBlueprint) return;
    
    if (!state.currentBlueprint.main_questions || state.currentBlueprint.main_questions.length === 0) {
        const total = state.currentBlueprint.paper_total || 30;
        loadSampleTemplate(total);
    }
    
    bpPaperTotal.value = state.currentBlueprint.paper_total || 30;
    mqList.innerHTML = '';
    
    const mqs = state.currentBlueprint.main_questions || [];
    
    mqs.forEach((mq, mqIdx) => {
        const card = document.createElement('div');
        card.className = 'card';
        card.style.padding = '0.75rem';
        card.style.borderLeft = '4px solid var(--primary-color)';
        card.style.marginBottom = '1rem';
        
        let sqHtml = '';
        const sqs = mq.subquestions || [];
        sqs.forEach((sq, sqIdx) => {
            sqHtml += `
                <div style="display:flex; gap:0.5rem; align-items:center; margin-top:0.25rem; padding-left:2rem;">
                    <input type="text" class="filter-input" style="width:60px;" value="${esc(sq.label)}" onchange="updateSq(${mqIdx}, ${sqIdx}, 'label', this.value)">
                    <input type="number" class="filter-input" style="width:70px;" step="0.5" min="0.5" value="${sq.marks}" onchange="updateSq(${mqIdx}, ${sqIdx}, 'marks', this.value)">
                    <span style="font-size:0.9rem; color:var(--text-secondary);">marks</span>
                    <button class="btn-icon" style="color:var(--error-color); padding:0 0.5rem;" onclick="removeSq(${mqIdx}, ${sqIdx})">×</button>
                </div>
            `;
        });
        
        const unitsOptions = UNITS.map(u => `<option value="${u}" ${mq.unit === u ? 'selected' : ''}>${u}</option>`).join('');
        
        const attemptRuleHtml = `
            <select class="filter-input" style="width:180px;" onchange="updateMq(${mqIdx}, 'attempt_rule', this.value)">
                <option value="ALL" ${mq.attempt_rule === 'ALL' ? 'selected' : ''}>Parts (all compulsory)</option>
                <option value="ANY_N" ${mq.attempt_rule === 'ANY_N' ? 'selected' : ''}>Attempt any N out of M</option>
            </select>
        `;

        card.innerHTML = `
            <div style="display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; margin-bottom:0.5rem;">
                <input type="text" class="filter-input" style="width:60px; font-weight:bold;" value="${esc(mq.label)}" onchange="updateMq(${mqIdx}, 'label', this.value)">
                <input type="text" class="filter-input" style="flex:1; min-width:150px;" placeholder="Instruction..." value="${esc(mq.instruction || '')}" onchange="updateMq(${mqIdx}, 'instruction', this.value)">
                ${attemptRuleHtml}
                ${mq.attempt_rule === 'ANY_N' ? `
                    <input type="number" class="filter-input" style="width:60px;" min="1" title="Attempt Count" value="${mq.attempt_count || 1}" onchange="updateMq(${mqIdx}, 'attempt_count', this.value)">
                ` : ''}
                <select class="filter-input" style="width:120px;" onchange="updateMq(${mqIdx}, 'unit', this.value)">
                    <option value="">-- Unit --</option>
                    ${unitsOptions}
                </select>
                <div style="display:flex; align-items:center; gap:0.25rem;">
                    [<input type="number" class="filter-input" style="width:60px; text-align:center;" min="0.5" step="0.5" value="${mq.target_marks || 0}" onchange="updateMq(${mqIdx}, 'target_marks', this.value)">] marks
                </div>
                <button class="btn-icon" style="color:var(--error-color); font-weight:bold; padding:0 0.5rem;" onclick="removeMq(${mqIdx})">×</button>
            </div>
            
            <div style="display:flex; flex-direction:column; gap:0.25rem;">
                ${sqHtml}
                <div style="padding-left:2rem; margin-top:0.25rem;">
                    <button class="btn-text" style="font-size:0.85rem; cursor:pointer;" onclick="addSq(${mqIdx})">+ Add part</button>
                </div>
            </div>
        `;
        mqList.appendChild(card);
    });
    
    validateLocalMath();
}

window.updateMq = (idx, field, val) => {
    if (field === 'attempt_count') val = parseInt(val, 10);
    if (field === 'target_marks') val = parseFloat(val);
    const mq = state.currentBlueprint.main_questions[idx];
    mq[field] = val;
    if (field === 'attempt_rule') {
        if (val === 'ALL') {
            mq.instruction = 'Answer the following questions';
        } else if (val === 'ANY_N') {
            const count = mq.attempt_count || 2;
            mq.instruction = `Answer the following (Any ${count})`;
        }
    }
    if (field === 'attempt_count' && mq.attempt_rule === 'ANY_N') {
        if (!mq.instruction || mq.instruction.startsWith('Answer the following') || mq.instruction.startsWith('Attempt any')) {
            mq.instruction = `Answer the following (Any ${val})`;
        }
    }
    renderBlueprint();
};

window.removeMq = (idx) => {
    if (confirm('Are you sure you want to remove this main question?')) {
        state.currentBlueprint.main_questions.splice(idx, 1);
        renderBlueprint();
    }
};

window.addSq = (mqIdx) => {
    const mq = state.currentBlueprint.main_questions[mqIdx];
    if(!mq.subquestions) mq.subquestions = [];
    const nextChar = String.fromCharCode(65 + mq.subquestions.length);
    mq.subquestions.push({label: nextChar, marks: 1.0});
    renderBlueprint();
};

window.updateSq = (mqIdx, sqIdx, field, val) => {
    if(field === 'marks') val = parseFloat(val) || 0;
    state.currentBlueprint.main_questions[mqIdx].subquestions[sqIdx][field] = val;
    renderBlueprint();
};

window.removeSq = (mqIdx, sqIdx) => {
    if (confirm('Are you sure you want to remove this part?')) {
        state.currentBlueprint.main_questions[mqIdx].subquestions.splice(sqIdx, 1);
        renderBlueprint();
    }
};

btnAddMq.addEventListener('click', () => {
    if(!state.currentBlueprint) state.currentBlueprint = {};
    if(!state.currentBlueprint.main_questions) state.currentBlueprint.main_questions = [];
    const idx = state.currentBlueprint.main_questions.length + 1;
    state.currentBlueprint.main_questions.push({
        label: `Q.${idx}`,
        instruction: 'Answer the following questions',
        unit: 'Unit 1',
        attempt_rule: 'ALL',
        target_marks: 5,
        subquestions: [
            {label: 'A', marks: 5.0}
        ]
    });
    renderBlueprint();
});

function loadSampleTemplate(marks) {
    if (!state.currentBlueprint) state.currentBlueprint = {};
    state.currentBlueprint.paper_total = marks;
    
    if (marks === 30) {
        state.currentBlueprint.main_questions = [
            {
                label: 'Q.1', instruction: 'Answer the following questions', unit: 'Unit 1', attempt_rule: 'ALL', target_marks: 7,
                subquestions: [ {label: 'A', marks: 3.0}, {label: 'B', marks: 4.0} ]
            },
            {
                label: 'Q.2', instruction: 'Answer the following questions', unit: 'Unit 2', attempt_rule: 'ALL', target_marks: 7,
                subquestions: [ {label: 'A', marks: 3.0}, {label: 'B', marks: 4.0} ]
            },
            {
                label: 'Q.3', instruction: 'Answer the following (Any 2)', unit: 'Unit 3', attempt_rule: 'ANY_N', attempt_count: 2, target_marks: 8,
                subquestions: [ {label: '1', marks: 4.0}, {label: '2', marks: 4.0}, {label: '3', marks: 4.0} ]
            },
            {
                label: 'Q.4', instruction: 'Answer the following (Any 2)', unit: 'Unit 4', attempt_rule: 'ANY_N', attempt_count: 2, target_marks: 8,
                subquestions: [ {label: '1', marks: 4.0}, {label: '2', marks: 4.0}, {label: '3', marks: 4.0}, {label: '4', marks: 4.0} ]
            }
        ];
    } else {
        // 60 marks: 6 main questions of 10 marks each, with 5 marks subquestions
        state.currentBlueprint.main_questions = [
            {
                label: 'Q.1', instruction: 'Answer the following questions', unit: 'Unit 1', attempt_rule: 'ALL', target_marks: 10,
                subquestions: [ {label: 'A', marks: 5.0}, {label: 'B', marks: 5.0} ]
            },
            {
                label: 'Q.2', instruction: 'Answer the following questions', unit: 'Unit 2', attempt_rule: 'ALL', target_marks: 10,
                subquestions: [ {label: 'A', marks: 5.0}, {label: 'B', marks: 5.0} ]
            },
            {
                label: 'Q.3', instruction: 'Answer the following questions', unit: 'Unit 3', attempt_rule: 'ALL', target_marks: 10,
                subquestions: [ {label: 'A', marks: 5.0}, {label: 'B', marks: 5.0} ]
            },
            {
                label: 'Q.4', instruction: 'Answer the following questions', unit: 'Unit 4', attempt_rule: 'ALL', target_marks: 10,
                subquestions: [ {label: 'A', marks: 5.0}, {label: 'B', marks: 5.0} ]
            },
            {
                label: 'Q.5', instruction: 'Answer the following questions', unit: 'Unit 5', attempt_rule: 'ALL', target_marks: 10,
                subquestions: [ {label: 'A', marks: 5.0}, {label: 'B', marks: 5.0} ]
            },
            {
                label: 'Q.6', instruction: 'Answer the following questions', unit: 'Unit 6', attempt_rule: 'ALL', target_marks: 10,
                subquestions: [ {label: 'A', marks: 5.0}, {label: 'B', marks: 5.0} ]
            }
        ];
    }
}

bpPaperTotal.addEventListener('change', (e) => {
    const marks = parseInt(e.target.value, 10);
    if(state.currentBlueprint && state.currentBlueprint.main_questions && state.currentBlueprint.main_questions.length > 0) {
        if(!confirm("Changing total marks will overwrite your current blueprint layout. Continue?")) {
            // Revert dropdown if cancelled
            e.target.value = state.currentBlueprint.paper_total || 30;
            return;
        }
    }
    loadSampleTemplate(marks);
    renderBlueprint();
});

// Also, when the Blueprint view is first shown, if it's completely empty, load 30 marks automatically!
window.showBlueprintView = function() {
    if (typeof switchView === 'function') {
        switchView('blueprint');
    } else {
        document.querySelectorAll('.view-section').forEach(v => v.classList.add('hidden'));
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        const vp = document.getElementById('view-blueprint');
        const nb = document.getElementById('nav-blueprint');
        if (vp) vp.classList.remove('hidden');
        if (nb) nb.classList.add('active');
    }
    
    if (!state.currentBlueprint || !state.currentBlueprint.main_questions || state.currentBlueprint.main_questions.length === 0) {
        loadSampleTemplate(30);
        renderBlueprint();
    }
};


btnLoadSample.addEventListener('click', () => {
    if(state.currentBlueprint && state.currentBlueprint.main_questions && state.currentBlueprint.main_questions.length > 0) {
        if(!confirm("This will overwrite your current blueprint layout. Continue?")) return;
    }
    
    if (!state.currentBlueprint) state.currentBlueprint = {};
    
    loadSampleTemplate(30);
    renderBlueprint();
});

function getCombinations(arr, k) {
    const results = [];
    function helper(start, combo) {
        if (combo.length === k) {
            results.push([...combo]);
            return;
        }
        for (let i = start; i < arr.length; i++) {
            combo.push(arr[i]);
            helper(i + 1, combo);
            combo.pop();
        }
    }
    helper(0, []);
    return results;
}

function validateLocalMath() {
    const bp = state.currentBlueprint;
    if (!bp) return;
    
    let allocated = 0;
    let errors = [];
    
    (bp.main_questions || []).forEach(mq => {
        const sqs = mq.subquestions || [];
        const marks = sqs.map(s => s.marks);
        const target = mq.target_marks || 0;
        
        if (target <= 0) errors.push(`${mq.label} must have a positive target marks value.`);
        
        allocated += target; // We trust target for paper total, but validate subquestions against it
        
        if (mq.attempt_rule === 'ALL') {
            const sqSum = marks.reduce((a, b) => a + b, 0);
            if (sqSum !== target) {
                errors.push(`${mq.label} subquestions total (${sqSum}m) does not match the configured main-question target (${target}m).`);
            }
        } else if (mq.attempt_rule === 'ANY_N') {
            const k = mq.attempt_count || 1;
            if (k > marks.length) {
                errors.push(`${mq.label} requires attempting ${k} questions, but only ${marks.length} are available.`);
            } else {
                const sums = new Set(getCombinations(marks, k).map(combo => combo.reduce((a, b) => a + b, 0)));
                if (sums.size > 1) {
                    errors.push(`${mq.label} has unequal marks making "Attempt ${k}" mathematically ambiguous. Possible totals: ${Array.from(sums).join(', ')}`);
                } else if (sums.size === 1) {
                    const calcSum = Array.from(sums)[0];
                    if (calcSum !== target) {
                        errors.push(`${mq.label} attempted parts yield ${calcSum}m, which does not match the configured main-question target (${target}m).`);
                    }
                }
            }
        }
        
        if (!mq.unit) errors.push(`${mq.label} is missing a Unit assignment.`);
    });
    
    bpAllocatedMarks.textContent = allocated;
    const paperTarget = bp.paper_total || 30;
    
    if (allocated === paperTarget) {
        bpMarksStatus.textContent = 'Perfectly allocated!';
        bpMarksStatus.style.color = 'green';
    } else if (allocated > paperTarget) {
        bpMarksStatus.textContent = `Excess: ${allocated - paperTarget} marks`;
        bpMarksStatus.style.color = 'var(--error-color)';
        errors.push(`Total marks allocated (${allocated}) exceed paper total (${paperTarget}).`);
    } else {
        bpMarksStatus.textContent = `Remaining: ${paperTarget - allocated} marks`;
        bpMarksStatus.style.color = '#d97706'; // warning orange
        errors.push(`Total marks allocated (${allocated}) fall short of paper total (${paperTarget}).`);
    }
    
    if (errors.length > 0) {
        valArea.innerHTML = '<div style="color:var(--error-color); padding:1rem; background:#ffe6e6; border-radius:4px; font-size:0.9rem;"><strong>Blueprint is invalid:</strong><br>' + errors.map(e => '• ' + esc(e)).join('<br>') + '</div>';
        btnSaveBlueprint.disabled = true;
        btnGeneratePaper.disabled = true;
    } else {
        valArea.innerHTML = '<div style="color:green; padding:1rem; background:#e6ffe6; border-radius:4px; font-size:0.9rem;">Blueprint math is valid. You can save and generate!</div>';
        btnSaveBlueprint.disabled = false;
        btnGeneratePaper.disabled = false; 
    }
}

btnSaveBlueprint.addEventListener('click', async () => {
    if(!state.currentBlueprint) return;
    
    // Auto-create blueprint if ID is missing but bank exists
    if(!state.currentBlueprint.id && state.currentBank && state.currentBank.id) {
        btnSaveBlueprint.textContent = 'Saving...';
        try {
            const createRes = await fetch('/api/blueprints/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    bank_id: state.currentBank.id,
                    name: (state.currentBank.name || 'Bank') + ' Blueprint',
                    paper_total: state.currentBlueprint.paper_total || 30
                })
            });
            const createData = await createRes.json();
            if (createData.id) {
                state.currentBlueprint.id = createData.id;
            }
        } catch(e) {
            console.error("Auto-create blueprint failed", e);
        }
    }

    if(!state.currentBlueprint.id) {
        alert("Please upload and import a document first. A blueprint must be attached to a question bank.");
        return;
    }
    
    btnSaveBlueprint.textContent = 'Saving...';
    try {
        const res = await fetch('/api/blueprints/' + state.currentBlueprint.id, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(state.currentBlueprint)
        });
        const data = await res.json();
        
        if (data.error) throw new Error(data.error);
        if (data.validation && !data.validation.valid) {
            throw new Error(data.validation.errors.join('\n'));
        }
        
        state.currentBlueprint = data;
        renderBlueprint();
        btnSaveBlueprint.textContent = 'Saved!';
        btnGeneratePaper.disabled = false; 
        setTimeout(() => btnSaveBlueprint.textContent = 'Save Blueprint', 2000);
        
    } catch(e) {
        alert('Failed to save blueprint: ' + e.message);
        btnSaveBlueprint.textContent = 'Save Blueprint';
    }
});

window.loadBlueprint = async function(bpId) {
    try {
        const res = await fetch('/api/blueprints/' + bpId);
        const data = await res.json();
        state.currentBlueprint = data;
        renderBlueprint();
    } catch(e) {
        console.error('Failed to load blueprint', e);
    }
};
