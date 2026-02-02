from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from app.authentication import supabase

main_bp = Blueprint('main', __name__)

# ============================================================================
# SAMPLE DATA - Replace this with database queries later
# ============================================================================

classes = {
    '1': {
        'id': '1',
        'name': 'Fall 2025 - Math 211 - Section 2',
        'semester': 'Fall 2025',
        'students': [
            {
                'id': '1',
                'name': 'Adams, John',
                'learning_objectives': [
                    {'name': 'Learning Objective 1', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 2', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 3', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 4', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 5', 'top_score': 'M', 'second_score': 'M'},
                ]
            },
            {
                'id': '2',
                'name': 'Basic, Anna',
                'learning_objectives': [
                    {'name': 'Learning Objective 1', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 2', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 3', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 4', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 5', 'top_score': 'M', 'second_score': 'M'},
                ]
            },
            {
                'id': '3',
                'name': 'Smith, George W.',
                'learning_objectives': [
                    {'name': 'Learning Objective 1', 'top_score': 'M', 'second_score': 'R'},
                    {'name': 'Learning Objective 2', 'top_score': 'M', 'second_score': 'R'},
                    {'name': 'Learning Objective 3', 'top_score': 'R', 'second_score': 'X'},
                    {'name': 'Learning Objective 4', 'top_score': 'R', 'second_score': 'X'},
                    {'name': 'Learning Objective 5', 'top_score': 'M', 'second_score': 'M'},
                ]
            },
            {
                'id': '4',
                'name': 'Tucker, Cameron',
                'learning_objectives': [
                    {'name': 'Learning Objective 1', 'top_score': 'R', 'second_score': 'X'},
                    {'name': 'Learning Objective 2', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 3', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 4', 'top_score': 'M', 'second_score': 'R'},
                    {'name': 'Learning Objective 5', 'top_score': 'R', 'second_score': 'X'},
                ]
            },
        ]
    },
    '2': {
        'id': '2',
        'name': 'Fall 2025 - Math 101 - Section 1',
        'semester': 'Fall 2025',
        'students': [
            {
                'id': '5',
                'name': 'Johnson, Emily',
                'learning_objectives': [
                    {'name': 'Learning Objective 1', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 2', 'top_score': 'M', 'second_score': 'R'},
                    {'name': 'Learning Objective 3', 'top_score': 'R', 'second_score': 'R'},
                ]
            },
            {
                'id': '6',
                'name': 'Williams, Michael',
                'learning_objectives': [
                    {'name': 'Learning Objective 1', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 2', 'top_score': 'M', 'second_score': 'M'},
                    {'name': 'Learning Objective 3', 'top_score': 'M', 'second_score': 'M'},
                ]
            },
        ]
    }
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def organize_by_learning_objectives(students):
    lo_dict = {}
    for student in students:
        for lo in student['learning_objectives']:
            lo_name = lo['name']
            if lo_name not in lo_dict:
                lo_dict[lo_name] = {
                    'name': lo_name,
                    'students_with_2m': [],
                    'students_with_1m': [],
                    'students_with_0m': [],
                    'total_students': len(students)
                }
            m_count = 0
            if lo['top_score'] == 'M':
                m_count += 1
            if lo['second_score'] == 'M':
                m_count += 1
            student_data = {
                'name': student['name'],
                'top_score': lo['top_score'],
                'second_score': lo['second_score']
            }
            if m_count == 2:
                lo_dict[lo_name]['students_with_2m'].append(student_data)
            elif m_count == 1:
                lo_dict[lo_name]['students_with_1m'].append(student_data)
            else:
                lo_dict[lo_name]['students_with_0m'].append(student_data)
    
    learning_objectives = []
    for lo_name, lo_data in lo_dict.items():
        lo_data['two_m_count'] = len(lo_data['students_with_2m'])
        lo_data['one_m_count'] = len(lo_data['students_with_1m'])
        lo_data['zero_m_count'] = len(lo_data['students_with_0m'])
        learning_objectives.append(lo_data)
    return learning_objectives

# ============================================================================
# PAGE ROUTES
# ============================================================================

@main_bp.route("/")
def home():
    return render_template("login.html")

@main_bp.route("/login")
def login_page():
    return render_template("login.html")

@main_bp.route("/logout")
def logout():
    try:
        supabase.auth.sign_out()
        session.clear()
        return redirect('/')
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# DASHBOARD ROUTES
# ============================================================================

@main_bp.route("/student/dashboard")
def student_dashboard():
    if 'user_id' not in session:
        return redirect('/login')
    return render_template("student_view.html")

@main_bp.route("/instructor/dashboard")
def instructor_dashboard():
    if 'user_id' not in session:
        return redirect('/login')
    return render_template("instructor_FrontEnd.html", classes=classes)

# ============================================================================
# CLASS ROUTES
# ============================================================================

@main_bp.route("/class/<class_id>")
def class_detail(class_id):
    if class_id not in classes:
        return redirect(url_for('main.instructor_dashboard'))
    class_data = classes[class_id]
    students = class_data['students']
    learning_objectives = organize_by_learning_objectives(students)
    return render_template('class_detail.html',
                         class_id=class_id,
                         class_name=class_data['name'],
                         students=students,
                         learning_objectives=learning_objectives)

@main_bp.route("/select_class", methods=['POST'])
def select_class():
    class_id = request.form.get('class_id')
    if class_id and class_id in classes:
        return redirect(url_for('main.class_detail', class_id=class_id))
    return redirect(url_for('main.instructor_dashboard'))

@main_bp.route("/class/<class_id>/create_learning_objective")
def create_learning_objective(class_id):
    if class_id not in classes:
        return redirect(url_for('main.instructor_dashboard'))
    class_data = classes[class_id]
    return render_template('create_learning_objective.html',
                         class_id=class_id,
                         class_name=class_data['name'])

@main_bp.route("/class/<class_id>/update_grade")
def update_grade(class_id):
    if class_id not in classes:
        return redirect(url_for('main.instructor_dashboard'))
    class_data = classes[class_id]
    return render_template('update_grade.html',
                         class_id=class_id,
                         class_name=class_data['name'])

@main_bp.route("/class/<class_id>/upload_grades", methods=['POST'])
def upload_grades(class_id):
    if class_id not in classes:
        return redirect(url_for('main.instructor_dashboard'))
    if 'file' not in request.files:
        return "No file uploaded", 400
    file = request.files['file']
    if file.filename == '':
        return "No file selected", 400
    return redirect(url_for('main.class_detail', class_id=class_id))

@main_bp.route("/class/<class_id>/upload_learning_objective", methods=['POST'])
def upload_learning_objective(class_id):
    if class_id not in classes:
        return redirect(url_for('main.instructor_dashboard'))
    if 'file' not in request.files:
        return "No file uploaded", 400
    file = request.files['file']
    if file.filename == '':
        return "No file selected", 400
    return redirect(url_for('main.class_detail', class_id=class_id))

@main_bp.route("/add_class", methods=['POST'])
def add_class():
    if 'user_id' not in session or session.get('role') != 'instructor':
        return redirect('/login')
    name = request.form.get('name')
    number = request.form.get('number')
    semester = request.form.get('semester')
    start = request.form.get('start')
    end = request.form.get('end')
    days = request.form.get('days')
    new_id = str(len(classes) + 1)
    classes[new_id] = {
        'id': new_id,
        'name': f'{semester} - {number} - {name}',
        'semester': semester,
        'start_date': start,
        'end_date': end,
        'days': days,
        'students': []
    }
    return redirect(url_for('main.class_detail', class_id=new_id))

# ============================================================================
# API ROUTES - Authentication
# ============================================================================

@main_bp.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    name = data.get("name")
    role = data.get("role")
    
    try:
        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "data": {
                "full_name": name,
                "role": role
            }
        })
        
        if response.user:
            session['user_id'] = response.user.id
            session['role'] = role
            return jsonify({"success": True, "redirect": f"/{role}/dashboard"})
        else:
            return jsonify({"success": False, "message": "Sign-up failed. Please try again."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@main_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    selected_role = data.get("role")

    try:
        result = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })

        if result.user:
            user_metadata = result.user.user_metadata or {}
            actual_role = user_metadata.get('role')
            
            if not actual_role:
                supabase.auth.update_user({"data": {"role": selected_role}})
                actual_role = selected_role
            
            if actual_role != selected_role:
                return jsonify({
                    "success": False, 
                    "message": f"This account is registered as a {actual_role}, not a {selected_role}"
                })
            
            session['user_id'] = result.user.id
            session['role'] = actual_role
            return jsonify({"success": True, "redirect": f"/{actual_role}/dashboard"})
        else:
            return jsonify({"success": False, "message": "Invalid credentials"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@main_bp.route("/api/search", methods=['GET'])
def search():
    query = request.args.get('query', '').lower()
    view = request.args.get('view', 'students')
    class_id = request.args.get('class_id', '1')
    
    if class_id not in classes:
        return jsonify({'error': 'Class not found'}), 404
    
    class_data = classes[class_id]
    
    if view == 'students':
        filtered_students = [
            student for student in class_data['students']
            if query in student['name'].lower()
        ]
        return jsonify({'students': filtered_students})
    else:
        learning_objectives = organize_by_learning_objectives(class_data['students'])
        filtered_los = []
        for lo in learning_objectives:
            if query in lo['name'].lower():
                filtered_los.append(lo)
            else:
                matching_2m = [s for s in lo['students_with_2m'] if query in s['name'].lower()]
                matching_1m = [s for s in lo['students_with_1m'] if query in s['name'].lower()]
                matching_0m = [s for s in lo['students_with_0m'] if query in s['name'].lower()]
                if matching_2m or matching_1m or matching_0m:
                    filtered_lo = lo.copy()
                    filtered_lo['students_with_2m'] = matching_2m
                    filtered_lo['students_with_1m'] = matching_1m
                    filtered_lo['students_with_0m'] = matching_0m
                    filtered_lo['two_m_count'] = len(matching_2m)
                    filtered_lo['one_m_count'] = len(matching_1m)
                    filtered_lo['zero_m_count'] = len(matching_0m)
                    filtered_los.append(filtered_lo)
        return jsonify({'learning_objectives': filtered_los})