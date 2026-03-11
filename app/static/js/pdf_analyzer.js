/**
 * Grade Sheet PDF Upload and OCR Analysis
 * Handles PDF upload, Paddle OCR analysis, and grade extraction
 */

let uploadedPDFData = null;

// ============================================================================
// FILE UPLOAD HANDLING
// ============================================================================

function handleFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  
  // Validate file type
  const allowedTypes = ['application/pdf', 'image/jpeg', 'image/jpg'];
  if (!allowedTypes.includes(file.type)) {
    alert('Please select a PDF or JPG file');
    input.value = '';
    return;
  }
  
  // Validate file size (10MB max)
  const maxSize = 10 * 1024 * 1024;
  if (file.size > maxSize) {
    alert('File size must be less than 10MB');
    input.value = '';
    return;
  }
  
  // Show selected file name
  const fileName = document.getElementById('selectedFileName');
  fileName.textContent = `Selected: ${file.name}`;
  fileName.classList.remove('hidden');
  
  // Reset analyzed data so new file triggers re-analysis
  uploadedPDFData = null;
  
  // Enable next button
  const nextBtn = document.getElementById('nextBtn1');
  nextBtn.disabled = false;
  nextBtn.classList.remove('opacity-50', 'cursor-not-allowed');
}

// Drag and drop handling
document.addEventListener('DOMContentLoaded', function() {
  const dropZone = document.getElementById('dropZone');
  const dropArea = document.getElementById('dropArea');
  const pdfInput = document.getElementById('pdfInput');
  
  if (!dropZone) return; // Only run on pages with drop zone
  
  // Drag over
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropArea.classList.add('border-blue-500', 'bg-blue-50');
  });
  
  // Drag leave
  dropZone.addEventListener('dragleave', () => {
    dropArea.classList.remove('border-blue-500', 'bg-blue-50');
  });
  
  // Drop
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropArea.classList.remove('border-blue-500', 'bg-blue-50');
    
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      pdfInput.files = files;
      handleFileSelect(pdfInput);
    }
  });
  
  // Click to upload is handled natively by the <label> wrapping the <input>
});

// ============================================================================
// STEP NAVIGATION
// ============================================================================

function goToStep(stepNumber) {
  const currentStep = getCurrentStep();
  
  if (stepNumber > currentStep) {
    // Moving forward - validate current step
    if (!validateStep(currentStep)) {
      return;
    }
    
    // If moving to step 2 and PDF hasn't been analyzed yet, analyze it first
    if (stepNumber === 2 && currentStep === 1 && !uploadedPDFData) {
      analyzePDF();
      return; // Let analyzePDF handle the step transition
    }
  }
  
  // Hide all panels
  document.querySelectorAll('[id^="panel-"]').forEach(panel => {
    panel.classList.add('hidden');
  });
  
  // Show selected panel
  const panel = document.getElementById(`panel-${stepNumber}`);
  if (panel) {
    panel.classList.remove('hidden');
  }
  
  // Update step indicators
  updateStepIndicators(stepNumber);
}

function getCurrentStep() {
  const panels = document.querySelectorAll('[id^="panel-"]');
  for (let i = 1; i <= panels.length; i++) {
    const panel = document.getElementById(`panel-${i}`);
    if (panel && !panel.classList.contains('hidden')) {
      return i;
    }
  }
  return 1;
}

function validateStep(stepNumber) {
  if (stepNumber === 1) {
    // Check if PDF is selected
    const pdfInput = document.getElementById('pdfInput');
    if (!pdfInput.files || pdfInput.files.length === 0) {
      alert('Please select a PDF file');
      return false;
    }
  }
  return true;
}

function updateStepIndicators(currentStep) {
  for (let i = 1; i <= 4; i++) {
    const icon = document.getElementById(`step-icon-${i}`);
    const label = document.getElementById(`step-label-${i}`);
    const connector = document.getElementById(`connector-${i}`);
    
    if (i < currentStep) {
      // Completed steps
      icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-green-600 text-white transition-all';
      label.className = 'mt-2 text-xs font-semibold text-green-600 dark:text-green-400';
      icon.innerHTML = '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>';
    } else if (i === currentStep) {
      // Current step
      icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-blue-600 text-white transition-all';
      label.className = 'mt-2 text-xs font-semibold text-blue-600 dark:text-blue-400';
    } else {
      // Future steps
      icon.className = 'w-10 h-10 rounded-full flex items-center justify-center bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500 transition-all';
      label.className = 'mt-2 text-xs font-medium text-slate-400 dark:text-slate-500';
    }
    
    if (connector && i < currentStep) {
      connector.classList.add('bg-green-600');
      connector.classList.remove('bg-slate-200', 'dark:bg-slate-600');
    }
  }
}

// ============================================================================
// PDF ANALYSIS
// ============================================================================

async function analyzePDF() {
  const pdfInput = document.getElementById('pdfInput');
  const file = pdfInput.files[0];
  
  if (!file) {
    alert('Please select a PDF file');
    return;
  }
  
  // Show loading state
  const panel = document.getElementById('panel-1');
  const nextBtn = document.getElementById('nextBtn1');
  const originalText = nextBtn.innerHTML;
  
  nextBtn.disabled = true;
  nextBtn.innerHTML = '<svg class="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Analyzing PDF with OCR...';
  
  try {
    // Prepare form data
    const formData = new FormData();
    formData.append('pdf', file);
    
    // Send to backend
    const response = await fetch('/api/analyze-grade-pdf', {
      method: 'POST',
      body: formData
    });
    
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || 'Failed to analyze PDF');
    }
    
    const result = await response.json();
    
    if (!result.success) {
      throw new Error(result.error || 'Analysis failed');
    }
    
    // Store extracted data
    uploadedPDFData = result.data;
    
    // Display results
    displayExtractedData(result.data);
    
    // Move to step 2
    goToStep(2);
    
  } catch (error) {
    console.error('Error analyzing PDF:', error);
    alert(`Error analyzing PDF: ${error.message}`);
  } finally {
    nextBtn.disabled = false;
    nextBtn.innerHTML = originalText;
  }
}

function displayExtractedData(data) {
  // Display learning objectives
  const loContainer = document.getElementById('extractedLOs');
  if (loContainer && data.learning_objectives) {
    if (data.learning_objectives.length > 0) {
      loContainer.innerHTML = data.learning_objectives
        .map(lo => `<span class="inline-block bg-blue-100 text-blue-800 px-3 py-1 rounded-full text-sm mr-2 mb-2">${lo}</span>`)
        .join('');
    } else {
      loContainer.innerHTML = '<p class="text-slate-500">No learning objectives detected. They will be auto-assigned.</p>';
    }
  }
  
  // Display student count
  const studentCount = document.getElementById('studentCount');
  if (studentCount && data.students) {
    studentCount.textContent = `${data.students.length} students found`;
  }
  
  // Display students table
  const studentsTable = document.getElementById('extractedStudentsTable');
  if (studentsTable && data.students) {
    const loHeaders = data.learning_objectives && data.learning_objectives.length > 0 
      ? data.learning_objectives 
      : [];
    
    let tableHTML = `
      <table class="w-full border-collapse text-sm">
        <thead>
          <tr class="bg-slate-100 dark:bg-slate-700">
            <th class="border border-slate-300 dark:border-slate-600 px-4 py-2 text-left font-semibold">Student Name</th>
            ${loHeaders.map(lo => `<th class="border border-slate-300 dark:border-slate-600 px-4 py-2 text-center font-semibold">${lo}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
    `;
    
    data.students.forEach((student, idx) => {
      const bgClass = idx % 2 === 0 ? 'bg-white dark:bg-slate-800' : 'bg-slate-50 dark:bg-slate-900';
      tableHTML += `
        <tr class="${bgClass}">
          <td class="border border-slate-300 dark:border-slate-600 px-4 py-2 font-medium">${student.name}</td>
          ${loHeaders.map(lo => `
            <td class="border border-slate-300 dark:border-slate-600 px-4 py-2 text-center">
              <span class="inline-block w-7 h-7 border border-slate-400 rounded flex items-center justify-center font-semibold text-sm bg-slate-50 dark:bg-slate-700">
                ${student.grades[lo] || '-'}
              </span>
            </td>
          `).join('')}
        </tr>
      `;
    });
    
    tableHTML += `
        </tbody>
      </table>
    `;
    
    studentsTable.innerHTML = tableHTML;
  }
}

// ============================================================================
// FORM SUBMISSION
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
  const gradeForm = document.getElementById('gradeUploadForm');
  
  if (gradeForm) {
    gradeForm.addEventListener('submit', async function(e) {
      e.preventDefault();
      
      if (!uploadedPDFData || !uploadedPDFData.students) {
        alert('No extracted data available. Please analyze a PDF first.');
        return;
      }
      
      // TODO: Process grades and submit to backend
      console.log('Submitting grades:', uploadedPDFData);
      alert('Grade processing - this will be implemented to save grades to database');
    });
  }
});
