/**
 * Grade Sheet PDF Upload and Gemini Analysis
 * Step order: Upload → Map Assignment → Review & Edit → Confirm
 * Features: speed-grader keyboard shortcuts, LO comparison, dark mode support
 */

let uploadedPDFData = null;
let mobilePollInterval = null;

function stopMobileUploadPoll() {
  if (mobilePollInterval) {
    clearInterval(mobilePollInterval);
    mobilePollInterval = null;
  }
}

/** Poll until phone uploads via QR handoff, then attach file to #pdfInput */
function startMobileUploadPoll() {
  if (!window.MOBILE_UPLOAD_TOKEN || !window.CLASS_ID) return;
  stopMobileUploadPoll();
  mobilePollInterval = setInterval(async () => {
    try {
      const r = await fetch(
        `/api/class/${window.CLASS_ID}/mobile-upload-status/${window.MOBILE_UPLOAD_TOKEN}`,
        { credentials: 'same-origin' }
      );
      if (!r.ok) return;
      const j = await r.json();
      if (!j.success || !j.ready) return;
      stopMobileUploadPoll();
      const fr = await fetch(
        `/api/class/${window.CLASS_ID}/mobile-upload-file/${window.MOBILE_UPLOAD_TOKEN}`,
        { credentials: 'same-origin' }
      );
      if (!fr.ok) {
        alert('Could not retrieve the file from your phone. Try scanning the QR code again.');
        return;
      }
      const blob = await fr.blob();
      const name = j.filename || 'photo.jpg';
      const pdfInput = document.getElementById('pdfInput');
      if (!pdfInput) return;
      const dt = new DataTransfer();
      dt.items.add(new File([blob], name, { type: blob.type || 'application/octet-stream' }));
      pdfInput.files = dt.files;
      handleFileSelect(pdfInput);
      const statusEl = document.getElementById('mobileUploadStatus');
      if (statusEl) {
        statusEl.textContent = 'Received from phone: ' + name;
        statusEl.classList.remove('hidden');
      }
    } catch (e) {
      console.error('Mobile upload poll:', e);
    }
  }, 2000);
}

// Speed-grader state
const gradeHistory = [];
const GRADE_KEYS = ['M', 'R', 'Q', 'P', 'X', 'A'];
const GRADE_MAP  = { 'Q': 'RQ' };  // Q key types RQ
const VALID_GRADES = new Set(['M', 'R', 'RQ', 'P', 'X', 'A']);

// LO comparison state
let assignmentLOs   = [];          // vendor_codes from selected assignment
let approvedExtraLOs = new Set();  // extra LOs the user approved

// ============================================================================
// FILE UPLOAD HANDLING
// ============================================================================

function handleFileSelect(input) {
  const file = input.files[0];
  if (!file) return;

  const allowedTypes = ['application/pdf', 'image/jpeg', 'image/jpg', 'image/png'];
  if (!allowedTypes.includes(file.type)) {
    alert('Please select a PDF, JPG, or PNG file');
    input.value = '';
    return;
  }

  const maxSize = 10 * 1024 * 1024;
  if (file.size > maxSize) {
    alert('File size must be less than 10MB');
    input.value = '';
    return;
  }

  const fileName = document.getElementById('selectedFileName');
  fileName.textContent = `Selected: ${file.name}`;
  fileName.classList.remove('hidden');

  uploadedPDFData = null;
  stopMobileUploadPoll();

  const nextBtn = document.getElementById('nextBtn1');
  nextBtn.disabled = false;
  nextBtn.classList.remove('opacity-50', 'cursor-not-allowed');
}

// Drag-and-drop + form submission wiring
document.addEventListener('DOMContentLoaded', function () {
  const dropZone = document.getElementById('dropZone');
  const dropArea = document.getElementById('dropArea');
  const pdfInput = document.getElementById('pdfInput');

  if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropArea.classList.add('border-blue-500', 'bg-blue-50');
    });
    dropZone.addEventListener('dragleave', () => {
      dropArea.classList.remove('border-blue-500', 'bg-blue-50');
    });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropArea.classList.remove('border-blue-500', 'bg-blue-50');
      if (e.dataTransfer.files.length > 0) {
        pdfInput.files = e.dataTransfer.files;
        handleFileSelect(pdfInput);
      }
    });
  }

  // Form submit handler
  const gradeForm = document.getElementById('uploadForm');
  if (gradeForm) {
    gradeForm.addEventListener('submit', handleFormSubmit);
  }

  startMobileUploadPoll();

  // Global Ctrl+Z undo
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
      e.preventDefault();
      undoLastGrade();
    }
  });
});

// ============================================================================
// STEP NAVIGATION  (1 Upload → 2 Map → 3 Review → 4 Confirm)
// ============================================================================

function goToStep(stepNumber) {
  const current = getCurrentStep();

  if (stepNumber > current) {
    if (!validateStep(current)) return;
    // Trigger Gemini analysis when leaving step 1 for the first time
    if (current === 1 && !uploadedPDFData) {
      analyzePDF();
      return;
    }
    // Render the extracted data table when entering step 3
    if (stepNumber === 3 && uploadedPDFData) {
      displayExtractedData(uploadedPDFData);
    }
    // Populate confirm summary when entering step 4
    if (stepNumber === 4) populateConfirmSummary();
  }

  // Show / hide panels
  for (let i = 1; i <= 4; i++) {
    const p = document.getElementById('panel-' + i);
    if (p) p.classList.toggle('hidden', i !== stepNumber);
  }
  updateStepIndicators(stepNumber);
}

function getCurrentStep() {
  for (let i = 1; i <= 4; i++) {
    const p = document.getElementById('panel-' + i);
    if (p && !p.classList.contains('hidden')) return i;
  }
  return 1;
}

function validateStep(step) {
  if (step === 1) {
    const pdfInput = document.getElementById('pdfInput');
    if (!pdfInput.files || pdfInput.files.length === 0) {
      alert('Please select a PDF file');
      return false;
    }
  }
  if (step === 2) {
    const sel = document.getElementById('assignmentSelect');
    if (!sel || !sel.value) {
      alert('Please select an assignment before continuing.');
      return false;
    }
  }
  return true;
}

function updateStepIndicators(currentStep) {
  const checkSVG = '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>';
  const stepIcons = {
    1: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>',
    2: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
    3: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>',
    4: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/></svg>'
  };

  for (let i = 1; i <= 4; i++) {
    const icon = document.getElementById('step-icon-' + i);
    const label = document.getElementById('step-label-' + i);
    const conn  = document.getElementById('connector-' + i);

    if (i < currentStep) {
      icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-green-600 text-white transition-all';
      label.className = 'mt-2 text-xs font-semibold text-green-600 dark:text-green-400';
      icon.innerHTML = checkSVG;
    } else if (i === currentStep) {
      icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-blue-600 text-white transition-all';
      label.className = 'mt-2 text-xs font-semibold text-blue-600 dark:text-blue-400';
      icon.innerHTML = stepIcons[i];
    } else {
      icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500 transition-all';
      label.className = 'mt-2 text-xs font-medium text-slate-400 dark:text-slate-500';
      icon.innerHTML = stepIcons[i];
    }

    if (conn) {
      if (i < currentStep) {
        conn.className = 'h-px w-24 md:w-32 mx-1 mb-5 transition-all bg-green-600';
      } else {
        conn.className = 'h-px w-24 md:w-32 mx-1 mb-5 transition-all bg-slate-200 dark:bg-slate-600';
      }
    }
  }
}

// ============================================================================
// ASSIGNMENT SELECTION  &  LO COMPARISON  (Step 2)
// ============================================================================

function onAssignmentSelected() {
  const sel = document.getElementById('assignmentSelect');
  if (!sel || !sel.value) return;

  const data = (window.ASSIGNMENTS_DATA || []).find(a => String(a.id) === String(sel.value));
  if (!data) return;

  // Extract vendor_codes from assignment_objectives → learning_objectives
  assignmentLOs = [];
  (data.assignment_objectives || []).forEach(ao => {
    const lo = ao.learning_objectives;
    if (lo && lo.vendor_code) assignmentLOs.push(lo.vendor_code);
  });

  approvedExtraLOs.clear();

  // If extracted data already exists, run comparison immediately
  if (uploadedPDFData) runLOComparison();
}

function runLOComparison() {
  const section = document.getElementById('loComparisonSection');
  const content = document.getElementById('loComparisonContent');
  if (!section || !content || !uploadedPDFData) return;

  const scannedLOs = uploadedPDFData.learning_objectives || [];
  const assignSet = new Set(assignmentLOs.map(v => v.toUpperCase()));

  const matched = [];
  const extra   = [];
  scannedLOs.forEach(lo => {
    if (assignSet.has(lo.toUpperCase())) matched.push(lo);
    else extra.push(lo);
  });

  const missing = assignmentLOs.filter(v => !scannedLOs.map(s => s.toUpperCase()).includes(v.toUpperCase()));

  let html = '';

  if (matched.length) {
    html += '<p class="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Matched</p><div class="flex flex-wrap gap-2 mb-3">';
    matched.forEach(lo => { html += `<span class="lo-matched px-2.5 py-1 rounded-full text-xs font-semibold">${escapeHTML(lo)}</span>`; });
    html += '</div>';
  }
  if (extra.length) {
    html += '<p class="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Extra (found by Gemini but not in assignment)</p><div class="flex flex-wrap gap-2 mb-3">';
    extra.forEach(lo => {
      const checked = approvedExtraLOs.has(lo) ? 'checked' : '';
      html += `<label class="lo-extra px-2.5 py-1 rounded-full text-xs font-semibold cursor-pointer flex items-center gap-1.5">
        <input type="checkbox" class="accent-amber-600" ${checked} onchange="toggleExtraLO('${escapeHTML(lo)}', this.checked)">
        ${escapeHTML(lo)}
      </label>`;
    });
    html += '</div>';
  }
  if (missing.length) {
    html += '<p class="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Missing (in assignment but not scanned)</p><div class="flex flex-wrap gap-2 mb-3">';
    missing.forEach(lo => { html += `<span class="lo-missing px-2.5 py-1 rounded-full text-xs font-semibold">${escapeHTML(lo)}</span>`; });
    html += '</div>';
  }

  if (!matched.length && !extra.length && !missing.length) {
    html = '<p class="text-sm text-slate-500 dark:text-slate-400">No learning objectives to compare yet.</p>';
  }

  content.innerHTML = html;
  section.classList.remove('hidden');
}

function toggleExtraLO(lo, checked) {
  if (checked) approvedExtraLOs.add(lo);
  else approvedExtraLOs.delete(lo);
}

// ============================================================================
// PDF / GEMINI ANALYSIS
// ============================================================================

async function analyzePDF() {
  const pdfInput = document.getElementById('pdfInput');
  const file = pdfInput.files[0];
  if (!file) { alert('Please select a PDF file'); return; }

  const nextBtn = document.getElementById('nextBtn1');
  const origHTML = nextBtn.innerHTML;
  nextBtn.disabled = true;
  nextBtn.innerHTML = '<svg class="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Analyzing with Gemini\u2026';

  try {
    const formData = new FormData();
    formData.append('pdf', file);

    const resp = await fetch('/api/analyze-grade-pdf', { method: 'POST', body: formData });
    if (!resp.ok) {
      const contentType = resp.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        const e = await resp.json();
        throw new Error(e.error || 'Failed to analyze PDF');
      }
      throw new Error(resp.status === 502 || resp.status === 504
        ? 'Request timed out. The AI service may be busy — please try again.'
        : `Server error (${resp.status}). Please try again.`);
    }

    const result = await resp.json();
    if (!result.success) throw new Error(result.error || 'Analysis failed');

    uploadedPDFData = result.data;

    // Pre-populate LO badges on step 3
    displayExtractedLOs(uploadedPDFData);

    // Jump to step 2 (Map Assignment)
    goToStep(2);
  } catch (err) {
    console.error('Gemini error:', err);
    alert('Error analyzing PDF: ' + err.message);
  } finally {
    nextBtn.disabled = false;
    nextBtn.innerHTML = origHTML;
  }
}

// ============================================================================
// DISPLAY EXTRACTED DATA  (Step 3 — Review & Edit)
// ============================================================================

function displayExtractedLOs(data) {
  const loContainer = document.getElementById('extractedLOs');
  if (!loContainer || !data.learning_objectives) return;
  if (data.learning_objectives.length > 0) {
    loContainer.innerHTML = data.learning_objectives
      .map(lo => `<span class="inline-block bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 px-3 py-1 rounded-full text-sm font-medium">${escapeHTML(lo)}</span>`)
      .join('');
  } else {
    loContainer.innerHTML = '<p class="text-slate-500 dark:text-slate-400 text-sm">No learning objectives detected.</p>';
  }
}

function displayExtractedData(data) {
  const studentCount = document.getElementById('studentCount');
  if (studentCount && data.students) {
    studentCount.textContent = `${data.students.length} students found`;
  }

  // Show selected assignment badge
  const badge = document.getElementById('selectedAssignmentBadge');
  const sel   = document.getElementById('assignmentSelect');
  if (badge && sel && sel.value) {
    badge.textContent = sel.options[sel.selectedIndex].text;
    badge.classList.remove('hidden');
  }

  const table = document.getElementById('extractedStudentsTable');
  if (!table || !data.students) return;

  // Build the set of approved LO codes: assignment-matched + user-approved extras
  const assignSet = new Set(assignmentLOs.map(v => v.toUpperCase()));
  const approvedSet = new Set([...assignSet, ...[...approvedExtraLOs].map(v => v.toUpperCase())]);

  // Filter LO headers: only show columns the user approved
  const allLOs = (data.learning_objectives && data.learning_objectives.length > 0)
    ? data.learning_objectives : [];
  const loHeaders = approvedSet.size > 0
    ? allLOs.filter(lo => approvedSet.has(lo.toUpperCase()))
    : allLOs;  // fallback: show all if no assignment was selected

  // Update the LO badges on Step 3 to reflect filtered set
  const loContainer = document.getElementById('extractedLOs');
  if (loContainer) {
    if (loHeaders.length > 0) {
      loContainer.innerHTML = loHeaders
        .map(lo => {
          const isExtra = !assignSet.has(lo.toUpperCase());
          const cls = isExtra
            ? 'inline-block bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300 px-3 py-1 rounded-full text-sm font-medium'
            : 'inline-block bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 px-3 py-1 rounded-full text-sm font-medium';
          return `<span class="${cls}">${escapeHTML(lo)}${isExtra ? ' (extra)' : ''}</span>`;
        }).join('');
    } else {
      loContainer.innerHTML = '<p class="text-slate-500 dark:text-slate-400 text-sm">No approved learning objectives to display.</p>';
    }
  }

  // Determine which columns are numeric (EX / FEX style) vs mark-based
  const numericCols = new Set();
  loHeaders.forEach(lo => {
    const upper = lo.toUpperCase();
    if (upper.startsWith('EX') || upper.startsWith('FEX') || upper.startsWith('EXAM') || upper.startsWith('FINAL')) {
      numericCols.add(lo);
    }
  });

  let html = `<table class="w-full border-collapse text-sm">
    <thead><tr class="bg-slate-100 dark:bg-slate-700">
      <th class="border border-slate-300 dark:border-slate-600 px-4 py-2 text-left font-semibold text-slate-900 dark:text-white" style="min-width:240px">Student Name</th>
      ${loHeaders.map(lo => `<th class="border border-slate-300 dark:border-slate-600 px-2 py-2 text-center font-semibold text-slate-900 dark:text-white text-xs">${escapeHTML(lo)}</th>`).join('')}
    </tr></thead><tbody>`;

  data.students.forEach((student, rowIdx) => {
    const name = student.name || '';
    html += `<tr>
      <td class="border border-slate-300 dark:border-slate-600 px-2 py-1">
        <input type="text" class="name-input" value="${escapeHTML(name)}" data-row="${rowIdx}" oninput="updateExtractedStudentName(${rowIdx}, this.value)">
      </td>`;

    loHeaders.forEach((lo, colIdx) => {
      const raw = (student.grades && student.grades[lo]) ? student.grades[lo] : '';
      if (numericCols.has(lo)) {
        html += `<td class="border border-slate-300 dark:border-slate-600 px-1 py-1 text-center">
          <input type="text" class="grade-input-numeric" value="${escapeHTML(raw)}" data-row="${rowIdx}" data-col="${colIdx}" data-lo="${escapeHTML(lo)}"
            oninput="updateExtractedGrade(${rowIdx}, '${escapeHTML(lo)}', this.value)">
        </td>`;
      } else {
        const grade = VALID_GRADES.has(raw.toUpperCase()) ? raw.toUpperCase() : raw;
        html += `<td class="border border-slate-300 dark:border-slate-600 px-1 py-1 text-center">
          <div class="grade-cell" tabindex="0" data-grade="${escapeHTML(grade)}" data-row="${rowIdx}" data-col="${colIdx}" data-lo="${escapeHTML(lo)}" data-student-index="${rowIdx}">
            ${escapeHTML(grade) || '<span class="text-slate-300 dark:text-slate-600 text-xs select-none">\u2014</span>'}
          </div>
        </td>`;
      }
    });

    html += '</tr>';
  });

  html += '</tbody></table>';
  table.innerHTML = html;

  // Attach speed-grader keyboard handlers to all .grade-cell
  attachGradeCellHandlers();
}

function escapeHTML(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============================================================================
// SPEED-GRADER KEYBOARD SHORTCUTS
// ============================================================================

function attachGradeCellHandlers() {
  document.querySelectorAll('#extractedStudentsTable .grade-cell').forEach(cell => {
    cell.addEventListener('click', () => cell.focus());

    cell.addEventListener('keydown', (e) => {
      const k   = e.key.toUpperCase();
      const row = parseInt(cell.dataset.row);
      const col = parseInt(cell.dataset.col);

      if (GRADE_KEYS.includes(k)) {
        e.preventDefault();
        const grade = GRADE_MAP[k] || k;
        setCellGrade(cell, grade);
        moveFocus(row, col + 1);
      } else if (e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        setCellGrade(cell, '');
      } else if (e.key === 'Tab') {
        e.preventDefault();
        moveFocus(row, col + (e.shiftKey ? -1 : 1));
      } else if (e.key === 'ArrowRight') { e.preventDefault(); moveFocus(row, col + 1); }
        else if (e.key === 'ArrowLeft')  { e.preventDefault(); moveFocus(row, col - 1); }
        else if (e.key === 'ArrowDown')  { e.preventDefault(); moveFocus(row + 1, col); }
        else if (e.key === 'ArrowUp')    { e.preventDefault(); moveFocus(row - 1, col); }
        else if (e.key === 'Enter')      { e.preventDefault(); moveFocus(row + 1, 0); }
    });
  });
}

function setCellGrade(cell, grade) {
  const rowIdx = parseInt(cell.dataset.studentIndex);
  const lo     = cell.dataset.lo;
  const prev   = cell.dataset.grade || '';

  if (prev === grade) return;

  // Push undo
  gradeHistory.push({ cell, prev });

  // Update data model
  if (uploadedPDFData && uploadedPDFData.students && uploadedPDFData.students[rowIdx]) {
    const s = uploadedPDFData.students[rowIdx];
    if (!s.grades) s.grades = {};
    s.grades[lo] = grade;
  }

  // Update DOM
  cell.dataset.grade = grade;
  cell.innerHTML = grade
    ? escapeHTML(grade)
    : '<span class="text-slate-300 dark:text-slate-600 text-xs select-none">\u2014</span>';
}

function undoLastGrade() {
  if (!gradeHistory.length) return;
  const { cell, prev } = gradeHistory.pop();
  const rowIdx = parseInt(cell.dataset.studentIndex);
  const lo     = cell.dataset.lo;

  if (uploadedPDFData && uploadedPDFData.students && uploadedPDFData.students[rowIdx]) {
    const s = uploadedPDFData.students[rowIdx];
    if (!s.grades) s.grades = {};
    s.grades[lo] = prev;
  }

  cell.dataset.grade = prev;
  cell.innerHTML = prev
    ? escapeHTML(prev)
    : '<span class="text-slate-300 dark:text-slate-600 text-xs select-none">\u2014</span>';
}

function moveFocus(row, col) {
  // Try grade-cell first, then numeric input
  let next = document.querySelector(`#extractedStudentsTable .grade-cell[data-row="${row}"][data-col="${col}"]`);
  if (!next) next = document.querySelector(`#extractedStudentsTable .grade-input-numeric[data-row="${row}"][data-col="${col}"]`);
  if (next) { next.focus(); next.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
}

// ============================================================================
// DATA UPDATE HELPERS
// ============================================================================

function updateExtractedStudentName(idx, value) {
  if (!uploadedPDFData || !uploadedPDFData.students || !uploadedPDFData.students[idx]) return;
  uploadedPDFData.students[idx].name = value;
}

function updateExtractedGrade(idx, lo, value) {
  if (!uploadedPDFData || !uploadedPDFData.students || !uploadedPDFData.students[idx]) return;
  const s = uploadedPDFData.students[idx];
  if (!s.grades) s.grades = {};
  s.grades[lo] = value.trim().toUpperCase();
}

// ============================================================================
// CONFIRM SUMMARY  (Step 4)
// ============================================================================

function populateConfirmSummary() {
  const sel = document.getElementById('assignmentSelect');
  const nameBadge = document.getElementById('confirmAssignmentName');
  if (nameBadge && sel && sel.value) {
    nameBadge.textContent = sel.options[sel.selectedIndex].text;
  }

  const summary = document.getElementById('confirmSummary');
  if (!summary || !uploadedPDFData) return;

  const students = uploadedPDFData.students || [];
  const los      = uploadedPDFData.learning_objectives || [];
  let totalGrades = 0;
  students.forEach(s => { totalGrades += Object.keys(s.grades || {}).length; });

  summary.innerHTML = `<p><strong>${students.length}</strong> students &middot; <strong>${los.length}</strong> learning objectives &middot; <strong>${totalGrades}</strong> grade entries</p>`;
  if (approvedExtraLOs.size > 0) {
    summary.innerHTML += `<p class="mt-1 text-amber-600 dark:text-amber-400 text-xs">Including <strong>${approvedExtraLOs.size}</strong> extra LO(s) you approved.</p>`;
  }
}

// ============================================================================
// FORM SUBMISSION
// ============================================================================

async function handleFormSubmit(e) {
  e.preventDefault();

  if (!uploadedPDFData || !uploadedPDFData.students) {
    alert('No extracted data available. Please analyze a PDF first.');
    return;
  }

  const classId = window.CLASS_ID;
  const sel = document.getElementById('assignmentSelect');
  const assignmentId = sel ? sel.value : null;

  if (!classId) { alert('Missing class ID.'); return; }

  // Disable submit button and show loading state to prevent double-clicks
  const importBtn = document.getElementById('importBtn');
  if (importBtn) {
    importBtn.disabled = true;
    importBtn.innerHTML = '<svg class="animate-spin -ml-1 mr-2 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Importing\u2026';
    importBtn.classList.add('opacity-75', 'cursor-not-allowed');
  }

  // Build the set of LOs to include: assignment LOs + approved extras
  const includeLOs = new Set(assignmentLOs.map(v => v.toUpperCase()));
  approvedExtraLOs.forEach(v => includeLOs.add(v.toUpperCase()));

  // Filter each student's grades to only include the approved LOs
  const filteredStudents = uploadedPDFData.students.map(s => {
    const grades = {};
    Object.keys(s.grades || {}).forEach(lo => {
      if (includeLOs.size === 0 || includeLOs.has(lo.toUpperCase())) {
        grades[lo] = s.grades[lo];
      }
    });
    return { name: s.name, grades };
  });

  // Filter LO list similarly
  const filteredLOs = (uploadedPDFData.learning_objectives || []).filter(lo =>
    includeLOs.size === 0 || includeLOs.has(lo.toUpperCase())
  );

  const payload = {
    class_id: classId,
    assignment_id: assignmentId,
    students: filteredStudents,
    learning_objectives: filteredLOs
  };

  try {
    const resp = await fetch('/api/import-grades', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.error || 'Failed to import grades'); }
    const result = await resp.json();
    if (!result.success) throw new Error(result.error || 'Import failed');

    // Redirect to Reports tab so user can review imported data
    window.location.href = '/class/' + classId + '/reports';
  } catch (err) {
    console.error('Import error:', err);
    alert('Error importing grades: ' + (err.message || err));
    // Re-enable button so they can retry
    if (importBtn) {
      importBtn.disabled = false;
      importBtn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/></svg> Import Grades';
      importBtn.classList.remove('opacity-75', 'cursor-not-allowed');
    }
  }
}
