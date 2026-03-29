from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session # type: ignore
from app.authentication import supabase, supabase_admin
from app.models import Course, Grade, Student, Profile, Homework
from app.dao.ocr_analyzer import get_ocr_analyzer
from uuid import uuid4

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


def normalize_profile(enrollment):
    prof = enrollment.get('profiles', {})
    if isinstance(prof, list):
        prof = prof[0] if prof else {}
    return prof or {}


def get_free_passes_remaining(student_id, class_id, passes_allowed):
    try:
        result = supabase_admin.table("free_passes") \
            .select("passes_used") \
            .eq("student_id", student_id) \
            .eq("class_id", class_id) \
            .execute()
        used = result.data[0]['passes_used'] if result.data else 0
        return max(0, passes_allowed - used)
    except Exception:
        return passes_allowed


def _load_students_from_grades(class_id):
    """Return a list of students (with grades) by scanning grades for this class."""
    try:
        # Filter grades by the class via the related learning objective.
        # This reduces payload size and avoids server disconnects when grades are large.
        grades_result = supabase_admin.table("grades") \
            .select("student_id, learning_objective_id, top_score, second_score, learning_objectives(id, name, vendor_code, class_id, required_ms)") \
            .eq("learning_objectives.class_id", class_id) \
            .execute()
        grades = grades_result.data or []
    except Exception as e:
        print(f"Error loading grades for class {class_id}: {e}")
        grades = []

    students_by_id = {}
    for g in grades:
        lo = g.get('learning_objectives') or {}
        if lo.get('class_id') != class_id:
            continue
        student_id = g.get('student_id')
        if not student_id:
            continue

        if student_id not in students_by_id:
            # Ensure profile exists so the student shows up everywhere
            ensure_profile_exists(student_id, full_name=student_id, role='student')
            # Attempt to fetch a nicer name if it exists
            try:
                prof_res = supabase_admin.table("profiles").select("full_name").eq("id", student_id).single().execute()
                name = (prof_res.data or {}).get('full_name') or student_id
            except Exception:
                name = student_id
            students_by_id[student_id] = {
                'id': student_id,
                'name': name,
                'learning_objectives': []
            }

        students_by_id[student_id]['learning_objectives'].append({
            'learning_objective_id': str(g.get('learning_objective_id')) if g.get('learning_objective_id') is not None else None,
            'name': lo.get('name'),
            'vendor_code': lo.get('vendor_code'),
            'top_score': g.get('top_score'),
            'second_score': g.get('second_score'),
            'm_count': (1 if g.get('top_score') == 'M' else 0) + (1 if g.get('second_score') == 'M' else 0),
            'required_ms': lo.get('required_ms') or 2,
            'is_passed': ((1 if g.get('top_score') == 'M' else 0) + (1 if g.get('second_score') == 'M' else 0)) >= (lo.get('required_ms') or 2)
        })

    return list(students_by_id.values())


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
    
    auto_convert_m = False
    class_name = None
    if data:
        cleaned_grades = []
        for g in data.get('grades', []):
            lo_data = g.get('learning_objectives', {})
            top = g.get('top_score')
            second = g.get('second_score')
            m_count = (1 if top == 'M' else 0) + (1 if second == 'M' else 0)
            required = lo_data.get('required_ms', 2) or 2
            cleaned_grades.append({
                'name': lo_data.get('name', 'Unknown LO'),
                'top_score': top,
                'second_score': second,
                'm_count': m_count,
                'required_ms': required,
                'is_passed': m_count >= required
            })
        data['learning_objectives'] = cleaned_grades

        # Get class settings for auto-convert
        enrollments = data.get('enrollments', [])
        if enrollments:
            cls = enrollments[0].get('classes', {})
            if cls:
                auto_convert_m = cls.get('auto_convert_m', False)
                class_name = cls.get('name')

    return render_template("student_view.html", student=data, auto_convert_m=auto_convert_m, class_name=class_name)

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
        print(f"[ERROR] class_detail: get_full_class_data returned None for class_id={class_id}")
        return redirect(url_for('main.instructor_dashboard'))

    raw_enrollments = class_data.get('enrollments', [])
    all_students_for_modal = []
    students_for_template = []
    
    for enrollment in raw_enrollments:
        # profiles is now the expanded profile object
        student_profile = enrollment.get('profiles', {})
        if not student_profile:
            continue
            
        # Ensure student_profile is a dict, not just an ID
        if not isinstance(student_profile, dict):
            continue
            
        student_profile['learning_objectives'] = []
        for g in student_profile.get('grades', []):
            lo_data = g.get('learning_objectives', {})
            top = g.get('top_score')
            second = g.get('second_score')
            m_count = (1 if top == 'M' else 0) + (1 if second == 'M' else 0)
            required = (lo_data.get('required_ms') or 2)
            student_profile['learning_objectives'].append({
                'learning_objective_id': str(g.get('learning_objective_id')) if g.get('learning_objective_id') is not None else None,
                'name': lo_data.get('name', 'Unknown LO'),
                'top_score': top,
                'second_score': second,
                'm_count': m_count,
                'required_ms': required,
                'is_passed': m_count >= required
            })
        if 'name' not in student_profile:
            student_profile['name'] = student_profile.get('full_name', 'Unnamed Student')
        student_profile['muted'] = enrollment.get('muted', False)
        all_students_for_modal.append(student_profile)
        if not student_profile['muted']:
            students_for_template.append(student_profile)

    summary = organize_by_learning_objectives(students_for_template, class_data.get('learning_objectives', []))
    
    # Load assignments for the assignments view
    try:
        assignments_result = supabase_admin.table("assignments") \
            .select("*, assignment_objectives(learning_objective_id, learning_objectives(id, name, vendor_code))") \
            .eq("class_id", class_id) \
            .order("created_at") \
            .execute()
        assignments = assignments_result.data or []
    except Exception as e:
        print(f"Error loading assignments for class detail: {e}")
        assignments = []
    
    return render_template('class_detail.html', 
                            class_id=class_id, 
                            class_name=class_data.get('name'), 
                            students=students_for_template, 
                            all_students=all_students_for_modal,
                            learning_objectives=summary,
                            assignments=assignments,
                            auto_convert_m=class_data.get('auto_convert_m', False),
                            min_masteries=class_data.get('min_masteries', 2))

@main_bp.route("/class/<class_id>/add_student", methods=["POST"])
def add_student(class_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json()
    email = data.get('email', '').strip()
    name = data.get('name', '').strip()
    
    if not email or not name:
        return jsonify({"success": False, "error": "Email and name are required"}), 400
    
    try:
        # Always create a new student profile with a unique UUID
        # (students don't log in; professors add them, so same-name students are different people)
        student_id = str(uuid4())
        supabase_admin.table("profiles").insert({
            "id": student_id,
            "full_name": name,
            "role": "student"
        }).execute()
        
        # Check if already enrolled
        existing_enrollment = supabase_admin.table("enrollments").select("id").eq("class_id", class_id).eq("student_id", student_id).execute()
        
        if existing_enrollment.data:
            return jsonify({"success": False, "error": "Student is already enrolled in this class"}), 400
        
        # Add enrollment
        supabase_admin.table("enrollments").insert({
            "class_id": class_id,
            "student_id": student_id
        }).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/students/<student_id>/delete", methods=["POST"])
def delete_student_from_class(class_id, student_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    try:
        # Remove enrollment
        supabase_admin.table("enrollments").delete().eq("class_id", class_id).eq("student_id", student_id).execute()

        # Remove all grades for this student
        supabase_admin.table("grades").delete().eq("student_id", student_id).execute()

        # Remove the profile itself
        supabase_admin.table("profiles").delete().eq("id", student_id).execute()

        return jsonify({"success": True})
    except Exception as e:
        print(f"Error deleting student {student_id} from class {class_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/students")
def class_students(class_id):
    if 'user_id' not in session: return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    students = []
    for e in class_data.get('enrollments', []):
        prof = e.get('profiles', {})
        # Compute per-LO pass status based on required_ms
        cleaned_los = []
        for g in prof.get('grades', []):
            lo_data = g.get('learning_objectives', {})
            top = g.get('top_score')
            second = g.get('second_score')
            m_count = (1 if top == 'M' else 0) + (1 if second == 'M' else 0)
            required = (lo_data.get('required_ms') or 2)
            cleaned_los.append({
                'name': lo_data.get('name', 'Unknown LO'),
                'top_score': top,
                'second_score': second,
                'm_count': m_count,
                'required_ms': required,
                'is_passed': m_count >= required
            })
        prof['learning_objectives'] = cleaned_los
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        if not e.get('muted', False):
            students.append(prof)

    # If the teacher hasn't enrolled students yet, fall back to grades data
    # (useful when grades are imported before enrollments exist).
    if not students:
        students = _load_students_from_grades(class_id)

    return render_template("class_students.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            students=students)

@main_bp.route("/class/<class_id>/delete", methods=["POST"])
def delete_class(class_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    try:
        # Remove associated learning objectives and their related data
        lo_result = supabase_admin.table("learning_objectives").select("id").eq("class_id", class_id).execute()
        lo_ids = [lo.get('id') for lo in (lo_result.data or []) if lo.get('id')]

        if lo_ids:
            supabase_admin.table("assignment_objectives").delete().in_("learning_objective_id", lo_ids).execute()
            supabase_admin.table("grades").delete().in_("learning_objective_id", lo_ids).execute()
            supabase_admin.table("learning_objectives").delete().in_("id", lo_ids).execute()

        # Remove assignments, enrollments, and the class itself
        supabase_admin.table("assignments").delete().eq("class_id", class_id).execute()
        supabase_admin.table("enrollments").delete().eq("class_id", class_id).execute()
        supabase_admin.table("classes").delete().eq("id", class_id).execute()

        return redirect(url_for('main.instructor_dashboard'))
    except Exception as e:
        print(f"Error deleting class {class_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/students/<student_id>")
def class_student_detail(class_id, student_id):
    if 'user_id' not in session:
        return redirect(url_for('main.login_page'))

    class_data = Course.get_full_class_data(class_id)
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    student = None
    for e in class_data.get('enrollments', []):
        if e.get('muted', False):
            continue
        prof = e.get('profiles', {})
        if prof.get('id') == student_id:
            # Compute per-LO pass status based on required_ms
            cleaned_los = []
            for g in prof.get('grades', []):
                lo_data = g.get('learning_objectives', {})
                top = g.get('top_score')
                second = g.get('second_score')
                m_count = (1 if top == 'M' else 0) + (1 if second == 'M' else 0)
                required = (lo_data.get('required_ms') or 2)
                cleaned_los.append({
                    'name': lo_data.get('name', 'Unknown LO'),
                    'top_score': top,
                    'second_score': second,
                    'm_count': m_count,
                    'required_ms': required,
                    'is_passed': m_count >= required
                })
            prof['learning_objectives'] = cleaned_los
            if 'name' not in prof:
                prof['name'] = prof.get('full_name', 'Unnamed Student')
            student = prof
            break

    if not student:
        # Fallback to grades data when there is no enrollment
        for s in _load_students_from_grades(class_id):
            if s.get('id') == student_id:
                student = s
                break

    if not student:
        return redirect(url_for('main.class_students', class_id=class_id))

    return render_template("class_student_detail.html", class_id=class_id, class_name=class_data.get('name'), student=student)

@main_bp.route("/class/<class_id>/assignments")
def class_assignments(class_id):
    if 'user_id' not in session: return redirect(url_for('main.login_page'))
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

    # Load all LOs for the class (for assignment)
    try:
        los_result = supabase_admin.table("learning_objectives") \
            .select("id, name, vendor_code") \
            .eq("class_id", class_id) \
            .execute()
        all_los = los_result.data or []
    except Exception as e:
        print(f"Error loading LOs: {e}")
        all_los = []

    return render_template("class_assignments.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            assignments=assignments,
                            all_los=all_los)

@main_bp.route("/class/<class_id>/assignments/<assignment_id>/delete", methods=["POST"])
def delete_assignment(class_id, assignment_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    try:
        # Delete assignment_objectives first
        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
        # Delete assignment
        supabase_admin.table("assignments").delete().eq("id", assignment_id).eq("class_id", class_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/create_assignment", methods=["POST"])
def create_assignment(class_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json() or {}
    print(f"[DEBUG] create_assignment payload: {data}")

    # Validate required fields
    name = (data.get('name') or '').strip()
    homework_group = (data.get('homework_group') or '').strip()

    if not name or not homework_group:
        return jsonify({"success": False, "error": "Missing or invalid required fields (name, homework_group)."}), 400

    try:
        # Create new assignment
        result = supabase_admin.table("assignments").insert({
            "class_id": class_id,
            "name": name,
            "homework_group": homework_group,
            "date_returned": data.get('date_returned'),
            "revision_due": data.get('revision_due')
        }).execute()
        
        # Link selected LOs to this assignment
        if result.data:
            assignment_id = result.data[0]['id']
            for lo_id in data.get('selected_los', []):
                supabase_admin.table("assignment_objectives").insert({
                    "assignment_id": assignment_id,
                    "learning_objective_id": lo_id
                }).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        print(f"[ERROR] create_assignment failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/assignments/<assignment_id>/update", methods=["POST", "PUT"])
def update_assignment(class_id, assignment_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json()
    try:
        # Update assignment
        supabase_admin.table("assignments").update({
            "name": data['name'],
            "homework_group": data['homework_group'],
            "date_returned": data.get('date_returned'),
            "revision_due": data.get('revision_due')
        }).eq("id", assignment_id).eq("class_id", class_id).execute()
        
        # Update linked LOs
        # First delete existing
        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
        # Then insert new
        for lo_id in data.get('selected_los', []):
            supabase_admin.table("assignment_objectives").insert({
                "assignment_id": assignment_id,
                "learning_objective_id": lo_id
            }).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/delete_assignment/<assignment_id>", methods=["POST"], endpoint='delete_assignment_alt')
def delete_assignment_alt(class_id, assignment_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    try:
        # First delete assignment_objectives links
        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
        # Then delete the assignment
        supabase_admin.table("assignments").delete().eq("id", assignment_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/delete_lo/<lo_id>", methods=["POST"])
def delete_lo(class_id, lo_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    try:
        # First delete assignment_objectives links
        supabase_admin.table("assignment_objectives").delete().eq("learning_objective_id", lo_id).execute()
        # Then delete grades
        supabase_admin.table("grades").delete().eq("learning_objective_id", lo_id).execute()
        # Then delete the LO
        supabase_admin.table("learning_objectives").delete().eq("id", lo_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/class/<class_id>/reports")
def class_reports(class_id):
    if 'user_id' not in session: 
        return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    students = []
    learning_objectives = class_data.get('learning_objectives', [])
    lo_lookup = {str(lo.get('id')): lo for lo in learning_objectives}

    for e in class_data.get('enrollments', []):
        prof = e.get('profiles', {})
        enriched_los = []
        for grade in prof.get('grades', []) or []:
            lo_id = str(grade.get('learning_objective_id')) if grade.get('learning_objective_id') is not None else None
            lo_info = lo_lookup.get(lo_id) if lo_id is not None else None
            top = grade.get('top_score')
            second = grade.get('second_score')
            m_count = (1 if top == 'M' else 0) + (1 if second == 'M' else 0)
            required = (lo_info.get('required_ms') or 2) if lo_info else 2
            enriched = {
                'learning_objective_id': lo_id,
                'top_score': top,
                'second_score': second,
                'name': lo_info.get('name') if lo_info else None,
                'm_count': m_count,
                'required_ms': required,
                'is_passed': m_count >= required
            }
            enriched_los.append(enriched)

        prof['learning_objectives'] = enriched_los
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        if not e.get('muted', False):
            students.append(prof)

    if not students:
        students = _load_students_from_grades(class_id)

    # Load assignments with their linked LOs and dates
    try:
        assignments_result = supabase_admin.table("assignments") \
            .select("*, assignment_objectives(learning_objective_id, learning_objectives(id, name, vendor_code))") \
            .eq("class_id", class_id) \
            .order("created_at", desc=True) \
            .execute()
        assignments = assignments_result.data or []
    except Exception as e:
        print(f"Error loading assignments for reports: {e}")
        assignments = []

    return render_template("class_reports.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            students=students, 
                            learning_objectives=learning_objectives,
                            assignments=assignments)

@main_bp.route("/class/<class_id>/student/<student_id>/history")
def student_history(class_id, student_id):
    if 'user_id' not in session:
        return redirect(url_for('main.login_page'))
    
    class_data = Course.get_full_class_data(class_id)
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    # Get student enrollment and profile info
    try:
        enrollment = supabase_admin.table("enrollments").select(
            "student_id, profiles(id, full_name)"
        ).eq("class_id", class_id).eq("student_id", student_id).single().execute()
        enrollment_data = enrollment.data
    except Exception as e:
        print(f"Error loading student enrollment: {e}")
        return redirect(url_for('main.class_reports', class_id=class_id))

    if not enrollment_data:
        return redirect(url_for('main.class_reports', class_id=class_id))

    profile = enrollment_data.get('profiles', {})
    student_name = profile.get('full_name', 'Unknown Student')
    profile_id = profile.get('id')

    # Get all grades for this student (they're linked by profile/student_id)
    try:
        grades_resp = supabase_admin.table("grades").select(
            "*"
        ).eq("student_id", profile_id).execute()
        all_grades = grades_resp.data or []
    except Exception as e:
        print(f"Error loading grades: {e}")
        all_grades = []
    
    # Get all learning objectives for the class
    learning_objectives = class_data.get('learning_objectives', [])
    lo_lookup = {str(lo.get('id')): lo for lo in learning_objectives}
    
    # Organize grades by learning objective
    student_grades = {}
    for grade in all_grades:
        lo_id = str(grade.get('learning_objective_id')) if grade.get('learning_objective_id') is not None else None
        if lo_id not in student_grades:
            student_grades[lo_id] = []
        student_grades[lo_id].append(grade)

    # Build grade data for template
    lo_grade_data = []
    for lo in learning_objectives:
        lo_id = str(lo.get('id'))
        grades = student_grades.get(lo_id, [])
        lo_info = {
            'id': lo_id,
            'name': lo.get('name'),
            'vendor_code': lo.get('vendor_code'),
            'grades': grades
        }
        lo_grade_data.append(lo_info)

    return render_template("student_history.html",
                          class_id=class_id,
                          class_name=class_data['name'],
                          student_id=student_id,
                          student_name=student_name,
                          learning_objectives=lo_grade_data)

@main_bp.route("/class/<class_id>/speed_grader", endpoint='class_speed_grader')
def class_speed_grader(class_id):
    if 'user_id' not in session:
        return redirect(url_for('main.login_page'))
    class_data = Course.get_full_class_data(class_id)

    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    hw_passes_enabled = class_data.get('hw_passes_enabled', False)
    hw_passes_allowed = class_data.get('hw_passes_allowed', 2)

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

    raw_enrollments = class_data.get('enrollments', [])
    lo_lookup = {str(lo.get('id')): lo for lo in class_data.get('learning_objectives', [])}
    students = []
    for enrollment in raw_enrollments:
        prof = normalize_profile(enrollment)
        if not prof:
            continue
        # Enrich grades with top-level name so Jinja selectattr works
        enriched_los = []
        for g in prof.get('grades', []) or []:
            lo_nested = g.get('learning_objectives') or {}
            lo_id = str(g.get('learning_objective_id')) if g.get('learning_objective_id') is not None else None
            lo_info = lo_lookup.get(lo_id) if lo_id else None
            top = g.get('top_score')
            second = g.get('second_score')
            m_count = (1 if top == 'M' else 0) + (1 if second == 'M' else 0)
            required = (lo_info.get('required_ms') or 2) if lo_info else 2
            enriched_los.append({
                'learning_objective_id': lo_id,
                'top_score': top,
                'second_score': second,
                'name': lo_nested.get('name') or (lo_info.get('name') if lo_info else None),
                'm_count': m_count,
                'required_ms': required,
                'is_passed': m_count >= required,
            })
        prof['learning_objectives'] = enriched_los
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        if enrollment.get('muted', False):
            continue
        if hw_passes_enabled:
            prof['passes_remaining'] = get_free_passes_remaining(
                prof['id'], class_id, hw_passes_allowed
            )
        else:
            prof['passes_remaining'] = None
        students.append(prof)

    if not students:
        students = _load_students_from_grades(class_id)
        for prof in students:
            if hw_passes_enabled:
                prof['passes_remaining'] = get_free_passes_remaining(
                    prof['id'], class_id, hw_passes_allowed
                )
            else:
                prof['passes_remaining'] = None

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
                           lo_names=lo_names,
                           hw_passes_enabled=hw_passes_enabled,
                           hw_passes_allowed=hw_passes_allowed,
                           auto_convert_m=class_data.get('auto_convert_m', False),
                           min_masteries=class_data.get('min_masteries', 2))

@main_bp.route("/class/<class_id>/update_grade", methods=["GET", "POST"], endpoint='upload_grades')
def update_grade_handler(class_id):
    if 'user_id' not in session: 
        return redirect(url_for('main.login_page'))
    
    class_data = Course.get_full_class_data(class_id)

    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))
    
    if request.method == "POST":
        # Block upload if no assignments exist
        assignments_check = supabase_admin.table("assignments") \
            .select("id") \
            .eq("class_id", class_id) \
            .limit(1) \
            .execute()
        if not assignments_check.data:
            return "Cannot upload grades: no assignments exist for this class", 400

        file = request.files.get('file')
        assignment_id = request.form.get('assignment_id')

        if not file or file.filename == '':
            return "No file selected", 400

        print(f"File uploaded for class {class_id}: {file.filename}, assignment_id={assignment_id}")

        # TODO: Parse and import grades from the uploaded file.
        # Currently we just redirect back to the class detail page.
        return redirect(url_for('main.class_detail', class_id=class_id))

    # Load assignments for this class WITH their linked learning objectives
    assignments = []
    try:
        assignments_result = supabase_admin.table("assignments") \
            .select("id,name,assignment_objectives(learning_objective_id,learning_objectives(id,name,vendor_code))") \
            .eq("class_id", class_id) \
            .order("created_at") \
            .execute()
        assignments = assignments_result.data or []
    except Exception as e:
        print(f"Error loading assignments for upload page: {e}")

    return render_template("update_grade.html", class_id=class_id, class_name=class_data.get('name'), assignments=assignments)

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
            date_returned = request.form.get('date_returned') or None
            revision_due = request.form.get('revision_due') or None
            selected_lo_ids = request.form.getlist('selected_los')  # Use getlist for multiple
            assignment_id = request.form.get('assignment_id')

            if assignment_name:
                try:
                    if assignment_id:
                        # Update existing assignment
                        supabase_admin.table("assignments").update({
                            "name": assignment_name,
                            "homework_group": hw_group,
                            "date_returned": date_returned,
                            "revision_due": revision_due
                        }).eq("id", assignment_id).execute()
                        
                        # Delete existing links and re-insert
                        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
                        for lo_id in selected_lo_ids:
                            if lo_id.strip():
                                supabase_admin.table("assignment_objectives").insert({
                                    "assignment_id": assignment_id,
                                    "learning_objective_id": lo_id
                                }).execute()
                    else:
                        # Create new assignment
                        result = supabase_admin.table("assignments").insert({
                            "class_id": class_id,
                            "name": assignment_name,
                            "homework_group": hw_group,
                            "date_returned": date_returned,
                            "revision_due": revision_due
                        }).execute()

                        # Link selected LOs to this assignment
                        if result.data:
                            assignment_id = result.data[0]['id']
                            for lo_id in selected_lo_ids:
                                if lo_id.strip():
                                    supabase_admin.table("assignment_objectives").insert({
                                        "assignment_id": assignment_id,
                                        "learning_objective_id": lo_id
                                    }).execute()
                except Exception as e:
                    print(f"Error saving assignment: {e}")

            return redirect(url_for('main.class_assignments', class_id=class_id))

        else:
            # Create a new learning objective
            lo_name = request.form.get('name', '').strip()
            lo_code = request.form.get('code', '').strip()
            lo_description = request.form.get('description', '').strip()
            lo_required_ms = request.form.get('required_ms', 2)

            if lo_name:
                try:
                    supabase_admin.table("learning_objectives").insert({
                        "class_id": class_id,
                        "vendor_code": lo_code or None,
                        "name": lo_name,
                        "description": lo_description or None,
                        "required_ms": int(lo_required_ms)
                    }).execute()
                except Exception as e:
                    print(f"Error creating LO: {e}")

            return redirect(url_for('main.class_assignments', class_id=class_id))

    # GET requests just redirect back to the objectives page (modal handles creation)
    return redirect(url_for('main.class_assignments', class_id=class_id))

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

        semester = request.form.get("semester", "")
        year = request.form.get("year", "")
        semester_full = f"{semester} {year}".strip() if year else semester

        new_class_data = {
            "name": request.form.get("name"),
            "semester": semester_full,
            "instructor_id": user_id,
        }
        # Optional columns — only include if the form provides them.
        # Each requires a matching column in the Supabase classes table.
        optional_fields = {
            "days": request.form.get("days", ""),
            "num_learning_objectives": int(request.form.get("num_learning_objectives") or 0),
            "min_masteries": int(request.form.get("min_masteries") or 2),
        }
        if request.form.get("auto_convert_m") == "1":
            optional_fields["auto_convert_m"] = True

        # Try inserting with all fields first; if a column is missing, retry without optional fields
        try:
            full_data = {**new_class_data, **optional_fields}
            supabase_admin.table("classes").insert(full_data).execute()
        except Exception as col_err:
            if 'PGRST204' in str(col_err) or 'schema cache' in str(col_err):
                # Fallback: insert only core columns
                supabase_admin.table("classes").insert(new_class_data).execute()
            else:
                raise
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


@main_bp.route("/api/class/<class_id>/assignments")
def api_class_assignments(class_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "unauthorized"}), 401
    try:
        result = supabase_admin.table("assignments") \
            .select("*") \
            .eq("class_id", class_id) \
            .order("created_at", desc=False) \
            .execute()
        return jsonify({"success": True, "assignments": result.data or []}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/save-grades", methods=["POST"])
def save_grades(class_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        grades_dict = data.get('grades', {})
        for key, grade_value in grades_dict.items():
            parts = key.split('|')
            if len(parts) == 2:
                student_id, lo_id = parts
                if lo_id and grade_value:
                    Grade.update_score(student_id=student_id, lo_id=lo_id, top_score=grade_value)
        return jsonify({"success": True})
    except Exception as e:
        print(f"saving grades failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/use_pass", methods=["POST"])
def use_free_pass(class_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        if not student_id:
            return jsonify({"success": False, "error": "student_id required"}), 400

        class_result = supabase_admin.table("classes") \
            .select("hw_passes_allowed") \
            .eq("id", class_id) \
            .single() \
            .execute()
        passes_allowed = (class_result.data or {}).get('hw_passes_allowed', 2)

        existing = supabase_admin.table("free_passes") \
            .select("id, passes_used") \
            .eq("student_id", student_id) \
            .eq("class_id", class_id) \
            .execute()

        if existing.data:
            current_used = existing.data[0]['passes_used']
            if current_used >= passes_allowed:
                return jsonify({"success": False, "error": "No passes remaining"}), 400
            supabase_admin.table("free_passes") \
                .update({"passes_used": current_used + 1}) \
                .eq("id", existing.data[0]['id']) \
                .execute()
            remaining = passes_allowed - (current_used + 1)
        else:
            supabase_admin.table("free_passes").insert({
                "student_id": student_id,
                "class_id": class_id,
                "passes_used": 1
            }).execute()
            remaining = passes_allowed - 1

        return jsonify({"success": True, "passes_remaining": remaining})
    except Exception as e:
        print(f"use pass failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/available_students", methods=["GET"])
def get_available_students(class_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        all_students = supabase_admin.table("profiles").select("id, full_name") \
            .eq("role", "student").execute().data or []
        enrolled_ids = {e['student_id'] for e in
                        supabase_admin.table("enrollments").select("student_id")
                        .eq("class_id", class_id).execute().data or []}
        available = sorted([s for s in all_students if s['id'] not in enrolled_ids],
                           key=lambda s: s['full_name'])
        return jsonify({"success": True, "students": available}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/add_student", methods=["POST"])
def api_add_student_to_class(class_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        student_name = data.get('student_name', '').strip()
        if not student_name:
            return jsonify({"success": False, "error": "student_name is required"}), 400
        student_id = str(uuid4())
        supabase_admin.table("profiles").insert({
            "id": student_id, "full_name": student_name, "role": "student"
        }).execute()
        supabase_admin.table("enrollments").insert({
            "class_id": class_id, "student_id": student_id
        }).execute()
        return jsonify({"success": True, "student_id": student_id, "student_name": student_name}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/toggle_mute", methods=["POST"])
def api_toggle_mute(class_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        muted = bool(data.get('muted', False))
        if not student_id:
            return jsonify({"success": False, "error": "student_id is required"}), 400
        supabase_admin.table("enrollments").update({"muted": muted}) \
            .eq("class_id", class_id).eq("student_id", student_id).execute()
        return jsonify({"success": True, "muted": muted}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/remove_student", methods=["POST"])
def api_remove_student_from_class(class_id):
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        if not student_id:
            return jsonify({"success": False, "error": "student_id is required"}), 400
        supabase_admin.table("enrollments").delete() \
            .eq("class_id", class_id).eq("student_id", student_id).execute()

        # Also delete the student's grades for LOs belonging to this class
        lo_res = supabase_admin.table("learning_objectives") \
            .select("id").eq("class_id", class_id).execute()
        lo_ids = [lo["id"] for lo in (lo_res.data or [])]
        if lo_ids:
            supabase_admin.table("grades").delete() \
                .eq("student_id", student_id) \
                .in_("learning_objective_id", lo_ids).execute()

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/import-grades", methods=["POST"])
def api_import_grades():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json() or {}
    class_id = data.get('class_id')
    assignment_id = data.get('assignment_id')
    students = data.get('students', []) or []
    extracted_los = data.get('learning_objectives', []) or []

    if not class_id:
        return jsonify({"success": False, "error": "Missing class_id"}), 400

    # Load existing LOs for the class (to map by name/code)
    try:
        los_resp = supabase_admin.table("learning_objectives").select("id,name,vendor_code").eq("class_id", class_id).execute()
        existing_los = los_resp.data or []
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to load learning objectives: {e}"}), 500

    lo_map = {}
    for lo in existing_los:
        if lo.get('vendor_code'):
            lo_map[lo['vendor_code'].strip().lower()] = lo['id']
        if lo.get('name'):
            lo_map[lo['name'].strip().lower()] = lo['id']

    # Create missing LOs from extracted list
    for lo_name in extracted_los:
        if not lo_name:
            continue
        key = lo_name.strip().lower()
        if key in lo_map:
            continue
        try:
            res = supabase_admin.table("learning_objectives").insert({
                "class_id": class_id,
                "vendor_code": lo_name,
                "name": lo_name
            }).execute()
            if res.data:
                lo_map[key] = res.data[0]['id']
        except Exception as e:
            print(f"Error creating LO '{lo_name}': {e}")

    imported = 0
    grade_rows = []

    # Link all extracted LOs to the selected assignment (if not already linked)
    if assignment_id:
        try:
            existing_links_resp = supabase_admin.table("assignment_objectives") \
                .select("learning_objective_id") \
                .eq("assignment_id", assignment_id) \
                .execute()
            already_linked = set(r["learning_objective_id"] for r in (existing_links_resp.data or []))

            for lo_name in extracted_los:
                if not lo_name:
                    continue
                lo_id = lo_map.get(lo_name.strip().lower())
                if lo_id and lo_id not in already_linked:
                    supabase_admin.table("assignment_objectives").insert({
                        "assignment_id": assignment_id,
                        "learning_objective_id": lo_id
                    }).execute()
                    already_linked.add(lo_id)
                    print(f"[IMPORT] Linked LO '{lo_name}' (id={lo_id}) to assignment {assignment_id}")
        except Exception as e:
            print(f"[IMPORT] Error linking LOs to assignment: {e}")

    for student in students:
        full_name = (student.get('name') or student.get('full_name') or '').strip()
        print(f"[IMPORT] Processing student: name='{student.get('name')}', full_name='{student.get('full_name')}', extracted='{full_name}'")
        if not full_name:
            print(f"[IMPORT] Skipping student - no name found")
            continue

        # Try to find existing profile by name (avoid single() throwing when no rows)
        profile_id = None
        try:
            profile_resp = supabase_admin.table("profiles").select("id").eq("full_name", full_name).limit(1).execute()
            if profile_resp.data and len(profile_resp.data) > 0:
                profile_id = profile_resp.data[0]['id']
                print(f"[IMPORT] Found existing profile for '{full_name}': {profile_id}")
        except Exception as e:
            print(f"[IMPORT] Error searching for profile for '{full_name}': {e}")
            profile_id = None

        if not profile_id:
            profile_id = str(uuid4())
            try:
                result = supabase_admin.table("profiles").insert({
                    "id": profile_id,
                    "full_name": full_name,
                    "role": "student"
                }).execute()
                print(f"[IMPORT] Created new profile for '{full_name}': {profile_id}")
            except Exception as e:
                print(f"[IMPORT] Error creating profile for '{full_name}': {e}")
                continue

        # Enroll student in class if not already enrolled
        try:
            enrollment_resp = supabase_admin.table("enrollments").select("id").eq("class_id", class_id).eq("student_id", profile_id).limit(1).execute()
            if not (enrollment_resp.data and len(enrollment_resp.data) > 0):
                supabase_admin.table("enrollments").insert({
                    "class_id": class_id,
                    "student_id": profile_id
                }).execute()
        except Exception as e:
            print(f"Error enrolling {full_name}: {e}")

        # Build grade rows to batch-upsert later
        grades = student.get('grades', {}) or {}
        for lo_name, mark in grades.items():
            if not lo_name or not mark:
                continue

            lo_id = lo_map.get(lo_name.strip().lower())
            if not lo_id:
                continue

            normalized = Grade.normalize_score(mark)
            if not normalized:
                continue

            grade_rows.append({
                "student_id": profile_id,
                "learning_objective_id": lo_id,
                "top_score": normalized
            })

        imported += 1

    # Batch upsert grades — overwrites existing scores for the same student+LO
    try:
        chunk_size = 150
        for i in range(0, len(grade_rows), chunk_size):
            chunk = grade_rows[i:i + chunk_size]
            # Add updated_at to each row so the timestamp refreshes on overwrite
            for row in chunk:
                row["updated_at"] = "now()"
            try:
                supabase_admin.table("grades").upsert(
                    chunk, on_conflict="student_id,learning_objective_id"
                ).execute()
                print(f"[IMPORT] Upserted {len(chunk)} grades")
            except Exception as upsert_err:
                print(f"[IMPORT] Grade upsert error: {upsert_err}")
    except Exception as e:
        print(f"Error processing grades in bulk: {e}")

    return jsonify({"success": True, "imported_students": imported, "imported_grades": len(grade_rows)}), 200


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
        
        # Allow PDF and image files
        allowed_extensions = ('.pdf', '.jpg', '.jpeg', '.png')
        if not pdf_file.filename.lower().endswith(allowed_extensions):
            return jsonify({
                "success": False,
                "error": "File must be a PDF, JPG, or PNG"
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
