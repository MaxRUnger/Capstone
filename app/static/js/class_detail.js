// Full list of 20 students for the class
const allStudents = [
    'Adams, John',
    'Basic, Anna',
    'Smith, George W.',
    'Tucker, Cameron',
    'Anderson, Emily',
    'Brown, Michael',
    'Chen, Sarah',
    'Davis, Robert',
    'Evans, Jessica',
    'Garcia, Daniel',
    'Harris, Olivia',
    'Jackson, William',
    'Johnson, Sophia',
    'Lee, Christopher',
    'Martinez, Isabella',
    'Miller, Matthew',
    'Moore, Ava',
    'Rodriguez, James',
    'Taylor, Emma',
    'Wilson, Alexander'
];

// Initialize student dropdowns when page loads
document.addEventListener('DOMContentLoaded', function() {
    updateAllStudentDropdowns();
});

// Update all student dropdowns to show only students not in this LO
function updateAllStudentDropdowns() {
    const loItems = document.querySelectorAll('.lo-item');
    
    loItems.forEach(loItem => {
        const loName = loItem.dataset.loName;
        const select = loItem.querySelector('.student-name-select');
        
        if (!select) return;
        
        // Get students already in this LO
        const existingStudents = new Set();
        loItem.querySelectorAll('.student-row .student-name').forEach(nameEl => {
            existingStudents.add(nameEl.textContent.trim());
        });
        
        // Clear and repopulate dropdown
        select.innerHTML = '<option value="">Select a student</option>';
        
        // Add students not in this LO
        allStudents.forEach(student => {
            if (!existingStudents.has(student)) {
                const option = document.createElement('option');
                option.value = student;
                option.textContent = student;
                select.appendChild(option);
            }
        });
        
        // Show message if all students are added
        if (existingStudents.size >= allStudents.length) {
            select.innerHTML = '<option value="">All students added</option>';
            select.disabled = true;
        }
    });
}

// Handle student selection (optional for future enhancements)
function handleStudentSelect(select) {
    // Optional: You can add any behavior here when a student is selected
}

// Add student to Learning Objective
function addStudentToLO(button) {
    const form = button.closest('.add-student-form');
    const loItem = button.closest('.lo-item');
    const loName = loItem.dataset.loName;
    
    // Get input values
    const studentName = form.querySelector('.student-name-select').value.trim();
    const topScore = form.querySelector('.top-score-input').value;
    const secondScore = form.querySelector('.second-score-input').value;
    
    // Validation
    if (!studentName) {
        alert('Please select a student');
        return;
    }
    if (!topScore) {
        alert('Please select a top highest score');
        return;
    }
    // if (!secondScore) {
    //     alert('Please select a second highest score');
    //     return;
    // }
    
    // Count M's to determine mastery level
    let mCount = 0;
    if (topScore === 'M') mCount++;
    if (secondScore === 'M') mCount++;
    
    // Find the appropriate mastery group
    const masteryGroup = loItem.querySelector(`.mastery-group[data-mastery="${mCount}"]`);
    const studentRowsContainer = masteryGroup.querySelector('.student-rows-container');
    
    // Create new student row
    const newRow = document.createElement('div');
    newRow.className = 'student-row';
    newRow.innerHTML = `
        <div class="student-name">${studentName}</div>
        <div class="score-badge">Top Highest: ${topScore}</div>
        <div class="score-badge">Second Highest: ${secondScore}</div>
    `;
    
    // Add to the appropriate section
    studentRowsContainer.appendChild(newRow);
    
    // Update count
    const countSpan = masteryGroup.querySelector('.mastery-count');
    const currentCount = countSpan.textContent.split('/');
    const newCount = parseInt(currentCount[0]) + 1;
    const total = parseInt(currentCount[1]) + 1;
    countSpan.textContent = `${newCount}/${total}`;
    
    // Update total in all mastery groups for this LO
    loItem.querySelectorAll('.mastery-count').forEach(span => {
        const parts = span.textContent.split('/');
        span.textContent = `${parts[0]}/${total}`;
    });
    
    // Add student to "By Students" view
    addStudentToStudentsView(studentName, loName, topScore, secondScore);
    
    // Clear form
    form.querySelector('.student-name-select').value = '';
    form.querySelector('.top-score-input').value = '';
    form.querySelector('.second-score-input').value = '';
    
    // Update all dropdowns to remove this student from other LOs
    updateAllStudentDropdowns();
    
    // Show success message
    const originalText = button.textContent;
    button.textContent = '✓ Added!';
    button.style.background = '#4ade80';
    setTimeout(() => {
        button.textContent = originalText;
        button.style.background = '';
    }, 2000);
}

// Add student to the "By Students" view
function addStudentToStudentsView(studentName, loName, topScore, secondScore) {
    const studentList = document.querySelector('#studentsView .student-list');
    
    // Check if student already exists
    let existingStudent = null;
    const studentItems = studentList.querySelectorAll('.student-item');
    
    for (let item of studentItems) {
        const nameElement = item.querySelector('.student-header span');
        if (nameElement && nameElement.textContent.trim() === studentName) {
            existingStudent = item;
            break;
        }
    }
    
    if (existingStudent) {
        // Add LO to existing student
        const loContent = existingStudent.querySelector('.learning-objectives-content');
        const newLORow = document.createElement('div');
        newLORow.className = 'lo-row';
        newLORow.innerHTML = `
            <div class="lo-name">${loName}</div>
            <div class="lo-score">Top Highest: ${topScore}</div>
            <div class="lo-score">Second Highest: ${secondScore}</div>
        `;
        loContent.appendChild(newLORow);
    } else {
        // Create new student entry
        const newStudentItem = document.createElement('div');
        newStudentItem.className = 'student-item';
        newStudentItem.innerHTML = `
            <div class="student-header" onclick="toggleStudent(this)">
                <span>${studentName}</span>
                <div class="chevron"></div>
            </div>
            <div class="learning-objectives-content">
                <div class="lo-row">
                    <div class="lo-name">${loName}</div>
                    <div class="lo-score">Top Highest: ${topScore}</div>
                    <div class="lo-score">Second Highest: ${secondScore}</div>
                </div>
            </div>
        `;
        
        // Insert in alphabetical order
        let inserted = false;
        for (let item of studentItems) {
            const nameElement = item.querySelector('.student-header span');
            if (nameElement && nameElement.textContent.trim() > studentName) {
                studentList.insertBefore(newStudentItem, item);
                inserted = true;
                break;
            }
        }
        
        // If not inserted, add to the end
        if (!inserted) {
            studentList.appendChild(newStudentItem);
        }
    }
}

// Toggle individual student
function toggleStudent(header) {
    const item = header.parentElement;
    item.classList.toggle('expanded');
}

// Toggle individual learning objective
function toggleObjective(header) {
    const item = header.parentElement;
    item.classList.toggle('expanded');
}

// Expand/Collapse all students
function expandAllStudents() {
    document.querySelectorAll('.student-item').forEach(item => {
        item.classList.add('expanded');
    });
}

function collapseAllStudents() {
    document.querySelectorAll('.student-item').forEach(item => {
        item.classList.remove('expanded');
    });
}

// Expand/Collapse all learning objectives
function expandAllObjectives() {
    document.querySelectorAll('.lo-item').forEach(item => {
        item.classList.add('expanded');
    });
}

function collapseAllObjectives() {
    document.querySelectorAll('.lo-item').forEach(item => {
        item.classList.remove('expanded');
    });
}

// Switch between views
function switchView(view) {
    const studentsView = document.getElementById('studentsView');
    const objectivesView = document.getElementById('objectivesView');
    const searchInput = document.getElementById('searchInput');

    if (view === 'students') {
        studentsView.classList.remove('hidden');
        objectivesView.classList.add('hidden');
        searchInput.placeholder = 'Search for Student';
    } else {
        studentsView.classList.add('hidden');
        objectivesView.classList.remove('hidden');
        searchInput.placeholder = 'Search for Learning Objectives';
    }
    
    // Clear search when switching views
    searchInput.value = '';
    handleSearch();
}

// Search functionality
function handleSearch() {
    const searchTerm = document.getElementById('searchInput').value.toLowerCase().trim();
    const studentsView = document.getElementById('studentsView');
    const objectivesView = document.getElementById('objectivesView');

    // Check which view is active
    if (!studentsView.classList.contains('hidden')) {
        // Search in Students View
        const studentItems = document.querySelectorAll('.student-item');
        
        studentItems.forEach(item => {
            const studentName = item.querySelector('.student-header span').textContent.toLowerCase();
            
            if (studentName.includes(searchTerm)) {
                item.style.display = '';
            } else {
                item.style.display = 'none';
            }
        });
    } else {
        // Search in Learning Objectives View
        const loItems = document.querySelectorAll('.lo-item');
        
        loItems.forEach(item => {
            const loName = item.querySelector('.lo-header span').textContent.toLowerCase();
            const studentRows = item.querySelectorAll('.student-row');
            let hasMatch = false;

            // Check if LO name matches
            if (loName.includes(searchTerm)) {
                item.style.display = '';
                // Show all student rows within this LO
                studentRows.forEach(row => {
                    row.style.display = '';
                });
                return;
            }

            // Check if any student name within this LO matches
            studentRows.forEach(row => {
                const studentName = row.querySelector('.student-name').textContent.toLowerCase();
                
                if (studentName.includes(searchTerm)) {
                    row.style.display = '';
                    hasMatch = true;
                } else {
                    row.style.display = 'none';
                }
            });

            // Show the LO item if any student matched, otherwise hide it
            if (hasMatch || searchTerm === '') {
                item.style.display = '';
                
                // Also check mastery groups - hide empty ones
                const masteryGroups = item.querySelectorAll('.mastery-group');
                masteryGroups.forEach(group => {
                    const visibleRows = Array.from(group.querySelectorAll('.student-row'))
                        .filter(row => row.style.display !== 'none');
                    
                    if (visibleRows.length === 0 && searchTerm !== '') {
                        group.style.display = 'none';
                    } else {
                        group.style.display = '';
                    }
                });
            } else {
                item.style.display = 'none';
            }
        });
    }
}