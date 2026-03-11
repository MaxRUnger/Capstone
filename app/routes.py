from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from app.authentication import supabase, supabase_admin
from app.models import Course, Grade, Student, Profile, Homework
from app.dao.ocr_analyzer import get_ocr_analyzer

main_bp = Blueprint('main', __name__, template_folder='templates')

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def organize_by_learning_objectives(students, learning_objectives):
    """Maps student grades to the relevant Learning Objectives for the UI."""
    lo_dict = {str(lo['id']): {
        'id': str(lo['id']),
        'name': lo['name'],
        'students_with_2m': [],
        'students_with_1m': [],
        'students_with_0m': [],
        'total_students': len(students)
    } for lo in learning_objectives}

    for student in students:
        student_grades = student.get('grades', [])
        for grade in student_grades:
            lo_id = str(grade['learning_objective_id'])
            if lo_id in lo_dict:
                m_count = 0
                top = grade.get('top_score')
                sec = grade.get('second_score')
                
                if top == 'M': m_count += 1
                if sec == 'M': m_count += 1
                
                student_data = {
                    'id': student['id'],
                    'name': student.get('full_name', 'Unknown Student'),
                    'top_score': top,
                    'second_score': sec
                }

                if m_count == 2: lo_dict[lo_id]['students_with_2m'].append(student_data)
                elif m_count == 1: lo_dict[lo_id]['students_with_1m'].append(student_data)
                else: lo_dict[lo_id]['students_with_0m'].append(student_data)
    
    return list(lo_dict.values())


def ensure_profile_exists(user_id, full_name=None, role='instructor'):
    """
    Upserts a row in the profiles table for the given user_id.
    Prevents foreign key errors when inserting classes or other records
    that reference profiles.id.
    """
    data = {"id": user_id, "role": role}
    if full_name:
        data["full_name"] = full_name
    try:
        supabase_admin.table("profiles").upsert(data, on_conflict="id").execute()
    except Exception:
        pass  # Silently continue if upsert fails — profile may already exist


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@main_bp.route("/")
@main_bp.route("/login")
def login_page():
    return render_template("login.html")

@main_bp.route("/signup")
def signup_page():
    return render_template("signup.html")

@main_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('main.login_page'))

@main_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    try:
        result = supabase.auth.sign_in_with_password({
            "email": data.get("email"), "password": data.get("password")
        })
        if result.user:
            actual_role = result.user.user_metadata.get('role', 'student')
            session['user_id'] = result.user.id
            session['role'] = actual_role
            session['full_name'] = result.user.user_metadata.get('full_name', '')
            # Store Supabase tokens for secure API usage
            if hasattr(result, 'session') and result.session:
                session['access_token'] = getattr(result.session, 'access_token', None)
                session['refresh_token'] = getattr(result.session, 'refresh_token', None)
            # Ensure profile exists on every login in case it was missed at signup
            ensure_profile_exists(
                result.user.id,
                full_name=result.user.user_metadata.get('full_name'),
                role=actual_role
            )
            return jsonify({"success": True, "redirect": f"/{actual_role}/dashboard"})
        return jsonify({"success": False, "message": "Invalid credentials"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@main_bp.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json()
    try:
        result = supabase.auth.sign_up({
            "email": data.get("email"),
            "password": data.get("password"),
            "options": {
                "data": {
                    "full_name": data.get("name"),
                    "role": "instructor"
                }
            }
        })

        if result.user:
            session['user_id'] = result.user.id
            session['role'] = 'instructor'
            session['full_name'] = data.get("name", "")
            # Store Supabase tokens for secure API usage
            if hasattr(result, 'session') and result.session:
                session['access_token'] = getattr(result.session, 'access_token', None)
                session['refresh_token'] = getattr(result.session, 'refresh_token', None)
            # Create profile row immediately so foreign keys work right away
            ensure_profile_exists(
                result.user.id,
                full_name=data.get("name"),
                role='instructor'
            )
            return jsonify({"success": True, "redirect": "/instructor/dashboard"})
        return jsonify({"success": False, "message": "Failed to create account. Please try again."})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============================================================================
# DASHBOARD ROUTES
# ============================================================================

@main_bp.route("/student/dashboard")
def student_dashboard():
    if 'user_id' not in session: 
        return redirect(url_for('main.login_page'))
    
    data = Student.get_dashboard_data(session['user_id'])
    
    if data:
        cleaned_grades = []
        for g in data.get('grades', []):
            cleaned_grades.append({
                'name': g.get('learning_objectives', {}).get('name', 'Unknown LO'),
                'top_score': g.get('top_score'),
                'second_score': g.get('second_score')
            })
        data['learning_objectives'] = cleaned_grades

    return render_template("student_view.html", student=data)

@main_bp.route("/instructor/dashboard")
def instructor_dashboard():
    if 'user_id' not in session or session.get('role') != 'instructor':
        return redirect(url_for('main.login_page'))
    db_classes = Course.get_all_for_instructor(session['user_id'])
    return render_template("instructor_select_class.html", classes=db_classes)

# ============================================================================
# CLASS MANAGEMENT ROUTES
# ============================================================================

@main_bp.route("/class/<class_id>")
def class_detail(class_id):
    if 'user_id' not in session: 
        return redirect(url_for('main.login_page'))
    
    class_data = Course.get_full_class_data(class_id)
    if not class_data: 
        return redirect(url_for('main.instructor_dashboard'))

    raw_enrollments = class_data.get('enrollments', [])
    students_for_template = []
    
    for enrollment in raw_enrollments:
        student_profile = enrollment.get('profiles', {})
        student_profile['learning_objectives'] = student_profile.get('grades', [])
        if 'name' not in student_profile:
            student_profile['name'] = student_profile.get('full_name', 'Unnamed Student')
        students_for_template.append(student_profile)

    summary = organize_by_learning_objectives(students_for_template, class_data.get('learning_objectives', []))
    
    return render_template('class_detail.html', 
                            class_id=class_id, 
                            class_name=class_data.get('name'), 
                            students=students_for_template, 
                            learning_objectives=summary)

@main_bp.route("/class/<class_id>/students")
def class_students(class_id):
    if 'user_id' not in session: return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    students = []
    for e in class_data.get('enrollments', []):
        prof = e.get('profiles', {})
        prof['learning_objectives'] = prof.get('grades', [])
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        students.append(prof)
        
    return render_template("class_students.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            students=students)

@main_bp.route("/class/<class_id>/objectives")
def class_objectives(class_id):
    if 'user_id' not in session: return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    return render_template("class_objectives.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            learning_objectives=class_data.get('learning_objectives', []))

@main_bp.route("/class/<class_id>/reports")
def class_reports(class_id):
    if 'user_id' not in session: return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    return render_template("class_reports.html", class_id=class_id, class_name=class_data['name'])

@main_bp.route("/class/<class_id>/speed_grader", endpoint='class_speed_grader')
def class_speed_grader(class_id):
    if 'user_id' not in session:
        return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)

    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    # Load assignments with their linked LOs
    try:
        assignments_result = supabase_admin.table("assignments") \
            .select("*, assignment_objectives(learning_objective_id, learning_objectives(id, name, vendor_code))") \
            .eq("class_id", class_id) \
            .order("created_at") \
            .execute()
        assignments = assignments_result.data or []
    except Exception as e:
        print(f"Error loading assignments: {e}")
        assignments = []

    # Load students with their grades
    raw_enrollments = class_data.get('enrollments', [])
    students = []
    for enrollment in raw_enrollments:
        prof = enrollment.get('profiles', {})
        prof['learning_objectives'] = prof.get('grades', [])
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        students.append(prof)

    # Get all LOs for the table columns (pass full objects)
    lo_names = []
    try:
        los_result = supabase_admin.table("learning_objectives") \
            .select("id, name, vendor_code") \
            .eq("class_id", class_id) \
            .execute()
        lo_names = los_result.data or []
    except Exception as e:
        print(f"Error loading LOs: {e}")

    return render_template("class_speed_grader.html",
                            class_id=class_id,
                            class_name=class_data.get('name'),
                            assignments=assignments,
                            students=students,
                            lo_names=lo_names)

@main_bp.route("/class/<class_id>/update_grade", methods=["GET", "POST"], endpoint='upload_grades')
def update_grade_handler(class_id):
    if 'user_id' not in session: 
        return redirect(url_for('main.login_page'))
    
    class_data = Course.get_full_class_data(class_id)

    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))
    
    if request.method == "POST":
        file = request.files.get('file')
        if not file or file.filename == '':
            return "No file selected", 400
        print(f"File uploaded for class {class_id}: {file.filename}")
        return redirect(url_for('main.class_detail', class_id=class_id))

    return render_template("update_grade.html", class_id=class_id, class_name=class_data.get('name'))

@main_bp.route("/class/<class_id>/create_learning_objective", methods=["GET", "POST"], endpoint='create_learning_objective')
def create_lo_handler(class_id):
    if 'user_id' not in session:
        return redirect(url_for('main.login_page'))

    class_data = Course.get_full_class_data(class_id)
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    if request.method == "POST":
        form_type = request.form.get('form_type', 'create_lo')

        if form_type == 'save_assignment':
            # Save assignment + link selected LOs
            assignment_name = request.form.get('assignment_name', '').strip()
            hw_group = request.form.get('hw_group', '')
            required_ms = request.form.get('required_ms', 2)
            date_returned = request.form.get('date_returned') or None
            revision_due = request.form.get('revision_due') or None
            selected_lo_ids = request.form.get('selected_los', '')

            if assignment_name:
                try:
                    result = supabase_admin.table("assignments").insert({
                        "class_id": class_id,
                        "name": assignment_name,
                        "homework_group": hw_group,
                        "required_ms": int(required_ms),
                        "date_returned": date_returned,
                        "revision_due": revision_due
                    }).execute()

                    # Link selected LOs to this assignment
                    if selected_lo_ids and result.data:
                        assignment_id = result.data[0]['id']
                        lo_ids = [lo_id.strip() for lo_id in selected_lo_ids.split(',') if lo_id.strip()]
                        for lo_id in lo_ids:
                            supabase_admin.table("assignment_objectives").insert({
                                "assignment_id": assignment_id,
                                "learning_objective_id": lo_id
                            }).execute()
                except Exception as e:
                    print(f"Error saving assignment: {e}")

            return redirect(url_for('main.class_speed_grader', class_id=class_id))

        else:
            # Create a new learning objective
            lo_name = request.form.get('name', '').strip()
            lo_code = request.form.get('code', '').strip()
            lo_description = request.form.get('description', '').strip()

            if lo_name:
                try:
                    supabase_admin.table("learning_objectives").insert({
                        "class_id": class_id,
                        "vendor_code": lo_code or None,
                        "name": lo_name,
                        "description": lo_description or None
                    }).execute()
                except Exception as e:
                    print(f"Error creating LO: {e}")

            return redirect(url_for('main.class_objectives', class_id=class_id))

    # GET requests just redirect back to the objectives page (modal handles creation)
    return redirect(url_for('main.class_objectives', class_id=class_id))

@main_bp.route("/support")
def support():
    return render_template("support.html")

# ============================================================================
# API & ACTION ROUTES
# ============================================================================

@main_bp.route("/add_class", methods=["POST"])
def add_class():
    if 'user_id' not in session:
        return redirect(url_for('main.login_page'))

    if not request.form.get("name"):
        return "Class name is required.", 400

    user_id = session['user_id']

    try:
        # Always upsert the profile first to satisfy the foreign key constraint.
        ensure_profile_exists(
            user_id,
            full_name=session.get('full_name'),
            role=session.get('role', 'instructor')
        )

        new_class_data = {
            "name": request.form.get("name"),
            "semester": request.form.get("semester"),
            "instructor_id": user_id
        }
        supabase_admin.table("classes").insert(new_class_data).execute()
        return redirect(url_for('main.instructor_dashboard'))
    except Exception as e:
        return f"Failed to create class: {str(e)}", 500


@main_bp.route("/api/update_grade", methods=["POST"], endpoint='api_update_grade')
def api_update_grade():
    if 'user_id' not in session: return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json()
    try:
        Grade.update_score(student_id=data['student_id'], lo_id=data['lo_id'], 
                            top_score=data['top_score'], second_score=data.get('second_score'))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/analyze-grade-pdf", methods=["POST"])
def analyze_grade_pdf():
    """
    Analyze a grade sheet (PDF or JPG) using OCR.
    Returns extracted student data and learning objectives.
    Works with both printed and handwritten grade sheets.
    
    Returns:
        JSON with:
        - success: bool
        - data: {students, learning_objectives, raw_text}
        - error: str (if failed)
    """
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    try:
        # Check if file is in request
        if 'pdf' not in request.files:
            return jsonify({
                "success": False,
                "error": "No file provided"
            }), 400
        
        pdf_file = request.files['pdf']
        
        if pdf_file.filename == '':
            return jsonify({
                "success": False,
                "error": "No file selected"
            }), 400
        
        # Allow PDF and JPG files
        allowed_extensions = ('.pdf', '.jpg', '.jpeg')
        if not pdf_file.filename.lower().endswith(allowed_extensions):
            return jsonify({
                "success": False,
                "error": "File must be a PDF or JPG"
            }), 400
        
        # Initialize OCR Analyzer
        print("[ROUTE] Getting OCR analyzer...")
        analyzer = get_ocr_analyzer()
        print(f"[ROUTE] Analyzer ready: {analyzer is not None}")
        
        if analyzer is None:
            return jsonify({
                "success": False,
                "error": "OCR not properly configured. Install dependencies: pip install pytesseract pdf2image pillow"
            }), 500
        
        # Read PDF content
        pdf_content = pdf_file.read()
        
        # Analyze PDF with OCR
        extracted_data = analyzer.analyze_pdf(pdf_content)
        
        return jsonify({
            "success": True,
            "data": {
                "students": extracted_data.get('students', []),
                "learning_objectives": extracted_data.get('learning_objectives', []),
                "raw_text": extracted_data.get('raw_text', '')
            }
        }), 200
        
    except Exception as e:
        print(f"Error analyzing PDF: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Failed to analyze PDF: {str(e)}"
        }), 500
