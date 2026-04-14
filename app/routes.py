import logging
import os
import socket
import threading
import time
from functools import wraps
from typing import Optional
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, abort, Response  # type: ignore
from app.authentication import supabase, supabase_admin
from app.models import Course, Grade, Student, Homework
from app.dao.gemini_analyzer import get_gemini_analyzer
from uuid import uuid4

logger = logging.getLogger(__name__)

main_bp = Blueprint('main', __name__, template_folder='templates')

# In-memory mobile upload handoff (single-process dev / one gunicorn worker).
# For multiple workers, replace with Redis or similar.
MOBILE_UPLOAD_TTL = 900  # 15 minutes
_pending_mobile_uploads = {}
_mobile_upload_lock = threading.Lock()


def _get_lan_ipv4() -> Optional[str]:
    """Best-effort primary LAN IPv4 for QR links when the dev server is opened via localhost."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.25)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return None


def _request_host_is_loopback() -> bool:
    host = (request.host or "").split(":")[0].lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host.startswith("127."):
        return True
    return False


def _public_base_url():
    """Base URL for QR / phone links. PUBLIC_BASE_URL wins (use for production, e.g. https://claritygrader.net)."""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    if _request_host_is_loopback():
        lan = _get_lan_ipv4()
        if lan:
            port = request.environ.get("SERVER_PORT", "5000")
            try:
                p = int(port)
            except ValueError:
                p = 5000
            return f"http://{lan}:{p}"
    return request.host_url.rstrip("/")


def _url_looks_like_loopback(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return "localhost" in u or "127.0.0.1" in u or "::1" in u


def _pending_put(token: str, class_id: str, user_id: str) -> None:
    with _mobile_upload_lock:
        _pending_mobile_uploads[token] = {
            "class_id": class_id,
            "user_id": user_id,
            "created": time.time(),
            "file": None,
            "filename": None,
            "content_type": None,
        }


def _pending_get(token: str):
    with _mobile_upload_lock:
        p = _pending_mobile_uploads.get(token)
        if not p:
            return None
        if time.time() - p["created"] > MOBILE_UPLOAD_TTL:
            del _pending_mobile_uploads[token]
            return None
        return p


def _pending_delete(token: str) -> None:
    with _mobile_upload_lock:
        _pending_mobile_uploads.pop(token, None)

DEFAULT_REQUIRED_MS = 2

# ============================================================================
# AUTH DECORATORS
# ============================================================================

def login_required(f):
    """Redirect to login page if the user has no active session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('main.login_page'))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    """Return 401 JSON if the user has no active session (for API routes)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def api_instructor_required(f):
    """Return 401 JSON if the user is not a logged-in instructor."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'instructor':
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


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
        logger.debug("Profile upsert skipped for %s — may already exist", user_id)


def normalize_profile(enrollment):
    prof = enrollment.get('profiles', {})
    if isinstance(prof, list):
        prof = prof[0] if prof else {}
    return prof or {}


def _batch_get_free_passes(student_ids, class_id):
    """Batch-fetch passes_used for a list of students. Returns {student_id: passes_used}."""
    if not student_ids:
        return {}
    try:
        resp = supabase_admin.table("free_passes") \
            .select("student_id, passes_used") \
            .eq("class_id", class_id) \
            .in_("student_id", student_ids) \
            .execute()
        return {r['student_id']: r['passes_used'] for r in (resp.data or [])}
    except Exception:
        return {}


def _load_students_from_grades(class_id):
    """Return a list of students (with grades) by scanning grades for this class."""
    try:
        grades_result = supabase_admin.table("grades") \
            .select("student_id, learning_objective_id, top_score, second_score, learning_objectives(id, name, vendor_code, class_id, required_ms)") \
            .eq("learning_objectives.class_id", class_id) \
            .execute()
        grades = grades_result.data or []
    except Exception as e:
        logger.error("Error loading grades for class %s: %s", class_id, e)
        grades = []

    # Collect unique student IDs from grade rows
    students_by_id = {}
    for g in grades:
        lo = g.get('learning_objectives') or {}
        if lo.get('class_id') != class_id:
            continue
        student_id = g.get('student_id')
        if not student_id:
            continue
        if student_id not in students_by_id:
            students_by_id[student_id] = {'id': student_id, 'name': student_id, 'raw_grades': []}
        students_by_id[student_id]['raw_grades'].append(g)

    # Batch-fetch profile names for all students in one query (fixes N+1)
    if students_by_id:
        unique_ids = list(students_by_id.keys())
        for sid in unique_ids:
            ensure_profile_exists(sid, full_name=sid, role='student')
        try:
            profiles_resp = supabase_admin.table("profiles") \
                .select("id, full_name").in_("id", unique_ids).execute()
            for p in (profiles_resp.data or []):
                pid = p.get('id')
                if pid in students_by_id:
                    students_by_id[pid]['name'] = p.get('full_name') or pid
        except Exception:
            pass  # Names fall back to student_id

    # Build lo_lookup from the LO data embedded in the grade rows
    lo_lookup = {}
    for g in grades:
        lo = g.get('learning_objectives') or {}
        lo_id = str(lo.get('id')) if lo.get('id') else None
        if lo_id and lo_id not in lo_lookup:
            lo_lookup[lo_id] = lo

    # Aggregate per-LO across assignments for each student
    for student in students_by_id.values():
        student['learning_objectives'] = _aggregate_lo_grades(student.pop('raw_grades'), lo_lookup)

    return list(students_by_id.values())


def _aggregate_lo_grades(raw_grades, lo_lookup):
    """Aggregate grades per LO across assignments.

    With per-assignment grading, a student may have multiple grade rows for the
    same LO (one per assignment).  This helper groups them and counts total M's
    so the student detail page can show e.g. "2 / 2 Ms".
    """
    lo_grades = {}
    for g in (raw_grades or []):
        lo_id = str(g.get('learning_objective_id')) if g.get('learning_objective_id') else None
        if not lo_id:
            continue
        if lo_id not in lo_grades:
            lo_info = lo_lookup.get(lo_id, {})
            lo_grades[lo_id] = {
                'learning_objective_id': lo_id,
                'name': lo_info.get('name', 'Unknown LO'),
                'vendor_code': lo_info.get('vendor_code', ''),
                'required_ms': lo_info.get('required_ms') or DEFAULT_REQUIRED_MS,
                'm_count': 0,
                'grades_list': [],
            }
        top = g.get('top_score')
        if top == 'M':
            lo_grades[lo_id]['m_count'] += 1
        lo_grades[lo_id]['grades_list'].append(top)

    results = []
    for lo in lo_grades.values():
        lo['is_passed'] = lo['m_count'] >= lo['required_ms']
        lo['top_score'] = lo['grades_list'][0] if lo['grades_list'] else None
        lo['second_score'] = lo['grades_list'][1] if len(lo['grades_list']) > 1 else None
        results.append(lo)
    return results


def _process_enrollments(class_data):
    """Extract students from enrollment data, aggregating grades per LO.

    Returns:
        (active_students, all_students, lo_lookup)
        - active_students: non-muted students with aggregated grades
        - all_students: all students (including muted) with aggregated grades
        - lo_lookup: {str(lo_id): lo_dict}
    """
    lo_lookup = {str(lo.get('id')): lo for lo in class_data.get('learning_objectives', [])}
    active_students = []
    all_students = []
    for e in class_data.get('enrollments', []):
        prof = e.get('profiles', {})
        if not prof or not isinstance(prof, dict):
            continue
        prof['learning_objectives'] = _aggregate_lo_grades(
            prof.get('grades', []) or [], lo_lookup
        )
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        prof['muted'] = e.get('muted', False)
        all_students.append(prof)
        if not prof['muted']:
            active_students.append(prof)
    return active_students, all_students, lo_lookup


def load_assignments_for_class(class_id, desc=False):
    """Load assignments with linked LOs for a class.

    Centralizes the repeated assignment query used by multiple route handlers.

    Returns:
        list of assignment dicts (empty list on error).
    """
    try:
        assignments_result = supabase_admin.table("assignments") \
            .select("*, assignment_objectives(learning_objective_id, learning_objectives(id, name, vendor_code))") \
            .eq("class_id", class_id) \
            .order("created_at", desc=desc) \
            .execute()
        return assignments_result.data or []
    except Exception as e:
        logger.error("Error loading assignments for class %s: %s", class_id, e)
        return []


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

@main_bp.route("/api/set-instructor-mode", methods=["POST"])
@api_login_required
def set_instructor_mode():
    data = request.get_json() or {}
    mode = data.get('mode')
    if mode not in ('mark', 'shelbi'):
        return jsonify({"success": False, "error": "Invalid mode"}), 400
    session['instructor_mode'] = mode
    return jsonify({"success": True, "mode": mode})

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
@login_required
def student_dashboard():
    data = Student.get_dashboard_data(session['user_id'])
    
    auto_convert_m = False
    class_name = None
    if data:
        # Build lo_lookup from embedded learning_objectives on each grade
        raw_grades = data.get('grades', []) or []
        lo_lookup = {}
        for g in raw_grades:
            lo = g.get('learning_objectives') or {}
            lo_id = str(lo.get('id')) if lo.get('id') else None
            if lo_id and lo_id not in lo_lookup:
                lo_lookup[lo_id] = lo
        data['learning_objectives'] = _aggregate_lo_grades(raw_grades, lo_lookup)

        # Get class settings for auto-convert
        enrollments = data.get('enrollments', [])
        if enrollments:
            cls = enrollments[0].get('classes', {})
            if cls:
                auto_convert_m = cls.get('auto_convert_m', False)
                class_name = cls.get('name')

    return render_template("student_view.html", student=data, auto_convert_m=auto_convert_m, class_name=class_name)

@main_bp.route("/instructor/dashboard")
@login_required
def instructor_dashboard():
    if session.get('role') != 'instructor':
        return redirect(url_for('main.login_page'))
    db_classes = Course.get_all_for_instructor(session['user_id'])
    return render_template("instructor_select_class.html", classes=db_classes)

# ============================================================================
# CLASS MANAGEMENT ROUTES
# ============================================================================

@main_bp.route("/class/<class_id>")
@login_required
def class_detail(class_id):
    class_data = Course.get_full_class_data(class_id)
    if not class_data:
        logger.error("class_detail: get_full_class_data returned None for class_id=%s", class_id)
        return redirect(url_for('main.instructor_dashboard'))

    students_for_template, all_students_for_modal, _ = _process_enrollments(class_data)
    summary = organize_by_learning_objectives(students_for_template, class_data.get('learning_objectives', []))
    assignments = load_assignments_for_class(class_id)

    overdue_raw = Grade.get_overdue_revisions(class_id)
    student_name_map = {s['id']: s.get('name', 'Unknown') for s in students_for_template}
    overdue_revisions = []
    for rev in overdue_raw:
        if rev['student_id'] in student_name_map:
            rev['student_name'] = student_name_map[rev['student_id']]
            overdue_revisions.append(rev)

    return render_template('class_detail.html', 
                            class_id=class_id, 
                            class_name=class_data.get('name'), 
                            students=students_for_template, 
                            all_students=all_students_for_modal,
                            learning_objectives=summary,
                            assignments=assignments,
                            overdue_revisions=overdue_revisions,
                            auto_convert_m=class_data.get('auto_convert_m', False),
                            min_masteries=class_data.get('min_masteries', 2))

@main_bp.route("/class/<class_id>/add_student", methods=["POST"])
@api_instructor_required
def add_student(class_id):
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
@api_instructor_required
def delete_student_from_class(class_id, student_id):
    try:
        # Remove enrollment for this class only
        supabase_admin.table("enrollments").delete().eq("class_id", class_id).eq("student_id", student_id).execute()

        # Remove grades scoped to this class
        lo_ids = Course.get_lo_ids_for_class(class_id)
        if lo_ids:
            supabase_admin.table("grades").delete().eq("student_id", student_id).in_("learning_objective_id", lo_ids).execute()

        # Remove homework scores for this class
        supabase_admin.table("homework_scores").delete().eq("student_id", student_id).eq("class_id", class_id).execute()

        return jsonify({"success": True})
    except Exception as e:
        logger.error("Error deleting student %s from class %s: %s", student_id, class_id, e)
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/students")
@login_required
def class_students(class_id):
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    students, _, _ = _process_enrollments(class_data)

    if not students:
        students = _load_students_from_grades(class_id)

    return render_template("class_students.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            students=students)

@main_bp.route("/class/<class_id>/delete", methods=["POST"])
@api_instructor_required
def delete_class(class_id):
    try:
        lo_ids = Course.get_lo_ids_for_class(class_id)

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
        logger.error("Error deleting class %s: %s", class_id, e)
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/students/<student_id>")
@login_required
def class_student_detail(class_id, student_id):
    class_data = Course.get_full_class_data(class_id)
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    lo_lookup = {str(lo.get('id')): lo for lo in class_data.get('learning_objectives', [])}

    student = None
    for e in class_data.get('enrollments', []):
        if e.get('muted', False):
            continue
        prof = e.get('profiles', {})
        if prof.get('id') == student_id:
            # Aggregate grades per LO across assignments
            prof['learning_objectives'] = _aggregate_lo_grades(prof.get('grades', []), lo_lookup)
            if 'name' not in prof:
                prof['name'] = prof.get('full_name', 'Unnamed Student')
            student = prof
            break

    if not student:
        for s in _load_students_from_grades(class_id):
            if s.get('id') == student_id:
                student = s
                break

    if not student:
        return redirect(url_for('main.class_students', class_id=class_id))

    return render_template("class_student_detail.html", class_id=class_id, class_name=class_data.get('name'), student=student)

@main_bp.route("/class/<class_id>/assignments")
@login_required
def class_assignments(class_id):
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    assignments = load_assignments_for_class(class_id)

    try:
        all_los = Course.get_learning_objectives(class_id)
    except Exception as e:
        logger.error("Error loading LOs for class %s: %s", class_id, e)
        all_los = []

    return render_template("class_assignments.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            assignments=assignments,
                            all_los=all_los)

@main_bp.route("/class/<class_id>/create_assignment", methods=["POST"])
@api_instructor_required
def create_assignment(class_id):
    
    data = request.get_json() or {}
    logger.debug("create_assignment payload: %s", data)

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
        
        # Link selected LOs to this assignment (batch insert)
        if result.data:
            assignment_id = result.data[0]['id']
            ao_rows = [{"assignment_id": assignment_id, "learning_objective_id": lo_id}
                       for lo_id in data.get('selected_los', []) if lo_id]
            if ao_rows:
                supabase_admin.table("assignment_objectives").insert(ao_rows).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        logger.error("create_assignment failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/assignments/<assignment_id>/update", methods=["POST", "PUT"])
@api_instructor_required
def update_assignment(class_id, assignment_id):
    
    data = request.get_json()
    try:
        # Update assignment
        supabase_admin.table("assignments").update({
            "name": data['name'],
            "homework_group": data['homework_group'],
            "date_returned": data.get('date_returned'),
            "revision_due": data.get('revision_due')
        }).eq("id", assignment_id).eq("class_id", class_id).execute()
        
        # Update linked LOs — delete existing, batch insert new
        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
        ao_rows = [{"assignment_id": assignment_id, "learning_objective_id": lo_id}
                   for lo_id in data.get('selected_los', []) if lo_id]
        if ao_rows:
            supabase_admin.table("assignment_objectives").insert(ao_rows).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/delete_assignment/<assignment_id>", methods=["POST"])
@api_instructor_required
def delete_assignment(class_id, assignment_id):
    
    try:
        # First delete assignment_objectives links
        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
        # Then delete the assignment
        supabase_admin.table("assignments").delete().eq("id", assignment_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/class/<class_id>/delete_lo/<lo_id>", methods=["POST"])
@api_instructor_required
def delete_lo(class_id, lo_id):
    
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
@login_required
def class_reports(class_id):
    class_data = Course.get_full_class_data(class_id)
    
    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    students, _, _ = _process_enrollments(class_data)
    learning_objectives = class_data.get('learning_objectives', [])

    if not students:
        students = _load_students_from_grades(class_id)

    # Load assignments with their linked LOs and dates
    assignments = load_assignments_for_class(class_id, desc=True)

    return render_template("class_reports.html", 
                            class_id=class_id, 
                            class_name=class_data['name'], 
                            students=students, 
                            learning_objectives=learning_objectives,
                            assignments=assignments)

@main_bp.route("/class/<class_id>/student/<student_id>/history")
@login_required
def student_history(class_id, student_id):
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
        logger.error("Error loading student enrollment: %s", e)
        return redirect(url_for('main.class_reports', class_id=class_id))

    if not enrollment_data:
        return redirect(url_for('main.class_reports', class_id=class_id))

    profile = enrollment_data.get('profiles', {})
    student_name = profile.get('full_name', 'Unknown Student')
    profile_id = profile.get('id')

    # Get all grades for this student with assignment names
    try:
        grades_resp = supabase_admin.table("grades").select(
            "*, assignments(name)"
        ).eq("student_id", profile_id).execute()
        all_grades = grades_resp.data or []
    except Exception as e:
        logger.error("Error loading grades: %s", e)
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
@login_required
def class_speed_grader(class_id):
    class_data = Course.get_full_class_data(class_id)

    if not class_data:
        return redirect(url_for('main.instructor_dashboard'))

    hw_passes_allowed = 2

    # Load assignments with their linked LOs
    assignments = load_assignments_for_class(class_id)

    raw_enrollments = class_data.get('enrollments', [])
    lo_lookup = {str(lo.get('id')): lo for lo in class_data.get('learning_objectives', [])}
    students = []
    for enrollment in raw_enrollments:
        prof = normalize_profile(enrollment)
        if not prof:
            continue
        if 'name' not in prof:
            prof['name'] = prof.get('full_name', 'Unnamed Student')
        if enrollment.get('muted', False):
            continue
        students.append(prof)

    if not students:
        students = _load_students_from_grades(class_id)

    # Batch-fetch free passes for all students in one query (fixes N+1)
    if students:
        student_ids = [s['id'] for s in students if s.get('id')]
        passes_map = _batch_get_free_passes(student_ids, class_id)
        for prof in students:
            prof['passes_remaining'] = max(0, hw_passes_allowed - passes_map.get(prof['id'], 0))

    lo_names = []
    try:
        lo_names = Course.get_learning_objectives(class_id)
    except Exception as e:
        logger.error("Error loading LOs: %s", e)

    return render_template("class_speed_grader.html",
                           class_id=class_id,
                           class_name=class_data.get('name'),
                           assignments=assignments,
                           students=students,
                           lo_names=lo_names,
                           hw_passes_allowed=hw_passes_allowed,
                           auto_convert_m=class_data.get('auto_convert_m', False),
                           min_masteries=class_data.get('min_masteries', 2))

@main_bp.route("/class/<class_id>/update_grade", methods=["GET", "POST"], endpoint='upload_grades')
@login_required
def update_grade_handler(class_id):
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

        logger.info("File uploaded for class %s: %s, assignment_id=%s", class_id, file.filename, assignment_id)

        # TODO: Parse and import grades from the uploaded file.
        # Currently we just redirect back to the class detail page.
        return redirect(url_for('main.class_detail', class_id=class_id))

    assignments = load_assignments_for_class(class_id)
    template_kwargs = {
        "class_id": class_id,
        "class_name": class_data.get("name"),
        "assignments": assignments,
    }
    if assignments:
        mobile_upload_token = str(uuid4())
        _pending_put(mobile_upload_token, class_id, session["user_id"])
        mobile_upload_url = (
            f"{_public_base_url()}/class/{class_id}/mobile-upload/{mobile_upload_token}"
        )
        template_kwargs["mobile_upload_token"] = mobile_upload_token
        template_kwargs["mobile_upload_url"] = mobile_upload_url
        template_kwargs["mobile_upload_url_is_loopback"] = _url_looks_like_loopback(mobile_upload_url)

    return render_template("update_grade.html", **template_kwargs)

@main_bp.route("/class/<class_id>/mobile-upload/<token>")
def mobile_upload_page(class_id, token):
    """Phone-friendly page to photograph or pick a grade sheet (opened via QR)."""
    p = _pending_get(token)
    if not p or p["class_id"] != class_id:
        return (
            render_template("mobile_upload.html", error="This link is invalid or has expired."),
            404,
        )
    return render_template("mobile_upload.html", class_id=class_id, token=token, error=None)


@main_bp.route("/api/mobile-upload/<token>", methods=["POST"])
def mobile_upload_receive(token):
    """Receive file from phone; token proves intent (short-lived, unguessable)."""
    p = _pending_get(token)
    if not p:
        return jsonify({"success": False, "error": "Invalid or expired link"}), 400

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    allowed_extensions = (".pdf", ".jpg", ".jpeg", ".png")
    if not f.filename.lower().endswith(allowed_extensions):
        return jsonify({"success": False, "error": "Use a PDF, JPG, or PNG"}), 400

    data = f.read()
    max_size = 10 * 1024 * 1024
    if len(data) > max_size:
        return jsonify({"success": False, "error": "File must be 10MB or smaller"}), 400

    content_type = f.mimetype or "application/octet-stream"
    with _mobile_upload_lock:
        if token not in _pending_mobile_uploads:
            return jsonify({"success": False, "error": "Link expired"}), 400
        _pending_mobile_uploads[token]["file"] = data
        _pending_mobile_uploads[token]["filename"] = f.filename
        _pending_mobile_uploads[token]["content_type"] = content_type

    return jsonify({"success": True})


@main_bp.route("/api/class/<class_id>/mobile-upload-status/<token>")
@api_login_required
def mobile_upload_status(class_id, token):
    """Desktop polls until the phone has uploaded a file."""
    p = _pending_get(token)
    if not p or p["class_id"] != class_id or p["user_id"] != session["user_id"]:
        return jsonify({"success": False, "error": "Not found"}), 404
    ready = p["file"] is not None
    return jsonify(
        {
            "success": True,
            "ready": ready,
            "filename": p["filename"] if ready else None,
        }
    )


@main_bp.route("/api/class/<class_id>/mobile-upload-file/<token>")
@api_login_required
def mobile_upload_file(class_id, token):
    """Return uploaded bytes once, then clear the handoff."""
    p = _pending_get(token)
    if not p or p["class_id"] != class_id or p["user_id"] != session["user_id"]:
        abort(404)
    if p["file"] is None:
        return jsonify({"success": False, "error": "No file yet"}), 404

    data = p["file"]
    filename = p["filename"] or "upload.jpg"
    content_type = p["content_type"] or "application/octet-stream"
    _pending_delete(token)

    return Response(
        data,
        mimetype=content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@main_bp.route("/class/<class_id>/create_learning_objective", methods=["GET", "POST"], endpoint='create_learning_objective')
@login_required
def create_lo_handler(class_id):
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
                        
                        # Delete existing links and batch re-insert
                        supabase_admin.table("assignment_objectives").delete().eq("assignment_id", assignment_id).execute()
                        ao_rows = [{"assignment_id": assignment_id, "learning_objective_id": lo_id}
                                   for lo_id in selected_lo_ids if lo_id.strip()]
                        if ao_rows:
                            supabase_admin.table("assignment_objectives").insert(ao_rows).execute()
                    else:
                        # Create new assignment
                        result = supabase_admin.table("assignments").insert({
                            "class_id": class_id,
                            "name": assignment_name,
                            "homework_group": hw_group,
                            "date_returned": date_returned,
                            "revision_due": revision_due
                        }).execute()

                        # Batch link selected LOs to this assignment
                        if result.data:
                            assignment_id = result.data[0]['id']
                            ao_rows = [{"assignment_id": assignment_id, "learning_objective_id": lo_id}
                                       for lo_id in selected_lo_ids if lo_id.strip()]
                            if ao_rows:
                                supabase_admin.table("assignment_objectives").insert(ao_rows).execute()
                except Exception as e:
                    logger.error("Error saving assignment: %s", e)

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
                    logger.error("Error creating LO: %s", e)

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
@login_required
def add_class():

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
@api_login_required
def api_update_grade():
    data = request.get_json()
    try:
        Grade.update_score(student_id=data['student_id'], lo_id=data['lo_id'], 
                            top_score=data['top_score'], second_score=data.get('second_score'),
                            assignment_id=data.get('assignment_id'))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/assignments")
@api_login_required
def api_class_assignments(class_id):
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
@api_login_required
def save_grades(class_id):
    try:
        data = request.get_json()
        grades_dict = data.get('grades', {})
        assignment_id = data.get('assignment_id')
        # Build batch of grade rows and upsert in one call
        grade_rows = []
        for key, grade_value in grades_dict.items():
            parts = key.split('|')
            if len(parts) == 2:
                student_id, lo_id = parts
                if lo_id and grade_value:
                    normalized = Grade.normalize_score(grade_value)
                    if normalized:
                        row = {"student_id": student_id, "learning_objective_id": lo_id, "top_score": normalized}
                        if assignment_id:
                            row["assignment_id"] = assignment_id
                        grade_rows.append(row)
        if grade_rows:
            supabase_admin.table("grades").upsert(
                grade_rows, on_conflict="student_id,learning_objective_id,assignment_id"
            ).execute()
        return jsonify({"success": True})
    except Exception as e:
        logger.error("saving grades failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/assignment/<assignment_id>/grades")
@api_login_required
def api_assignment_grades(class_id, assignment_id):
    """Return grades for a specific assignment, keyed by student_id|lo_id."""
    try:
        result = supabase_admin.table("grades") \
            .select("student_id, learning_objective_id, top_score") \
            .eq("assignment_id", assignment_id) \
            .execute()
        grades_map = {}
        for g in (result.data or []):
            key = f"{g['student_id']}|{g['learning_objective_id']}"
            grades_map[key] = g['top_score']

        # Also fetch HW% scores for this assignment (homework_group = assignment_id)
        hw_resp = supabase_admin.table("homework_scores") \
            .select("student_id, score_pct") \
            .eq("class_id", class_id) \
            .eq("homework_group", assignment_id) \
            .execute()
        hw_map = {r['student_id']: r['score_pct'] for r in (hw_resp.data or [])}

        # Compute revision eligibility per student (HW >= 65% or pass used)
        eligibility = {}
        for sid, score in hw_map.items():
            eligibility[sid] = score == -1 or (score is not None and score >= Homework.REVISION_THRESHOLD)

        return jsonify({"success": True, "grades": grades_map, "hw_scores": hw_map, "revision_eligible": eligibility})
    except Exception as e:
        logger.error("fetching assignment grades failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/save-hw-percentage", methods=["POST"])
@api_login_required
def save_hw_percentage(class_id):
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        score = data.get('score')
        assignment_id = data.get('assignment_id')
        if student_id is None or score is None or not assignment_id:
            return jsonify({"success": False, "error": "student_id, score, and assignment_id required"}), 400
        score = int(score)
        if score != -1:
            score = max(0, min(100, score))
        supabase_admin.table("homework_scores").upsert(
            {"student_id": student_id, "class_id": class_id, "homework_group": assignment_id, "score_pct": score},
            on_conflict="student_id,class_id,homework_group"
        ).execute()
        return jsonify({"success": True})
    except Exception as e:
        logger.error("saving hw percentage failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/use_pass", methods=["POST"])
@api_login_required
def use_free_pass(class_id):
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        if not student_id:
            return jsonify({"success": False, "error": "student_id required"}), 400

        passes_allowed = 2

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
        logger.error("use pass failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/return_pass", methods=["POST"])
@api_login_required
def return_free_pass(class_id):
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        if not student_id:
            return jsonify({"success": False, "error": "student_id required"}), 400

        passes_allowed = 2

        existing = supabase_admin.table("free_passes") \
            .select("id, passes_used") \
            .eq("student_id", student_id) \
            .eq("class_id", class_id) \
            .execute()

        if existing.data and existing.data[0]['passes_used'] > 0:
            current_used = existing.data[0]['passes_used']
            supabase_admin.table("free_passes") \
                .update({"passes_used": current_used - 1}) \
                .eq("id", existing.data[0]['id']) \
                .execute()
            remaining = passes_allowed - (current_used - 1)
        else:
            remaining = passes_allowed

        return jsonify({"success": True, "passes_remaining": remaining})
    except Exception as e:
        logger.error("return pass failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/class/<class_id>/available_students", methods=["GET"])
@api_login_required
def get_available_students(class_id):
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
@api_login_required
def api_add_student_to_class(class_id):
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
@api_instructor_required
def api_toggle_mute(class_id):
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
@api_login_required
def api_remove_student_from_class(class_id):
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        if not student_id:
            return jsonify({"success": False, "error": "student_id is required"}), 400
        supabase_admin.table("enrollments").delete() \
            .eq("class_id", class_id).eq("student_id", student_id).execute()

        # Also delete the student's grades for LOs belonging to this class
        lo_ids = Course.get_lo_ids_for_class(class_id)
        if lo_ids:
            supabase_admin.table("grades").delete() \
                .eq("student_id", student_id) \
                .in_("learning_objective_id", lo_ids).execute()

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@main_bp.route("/api/import-grades", methods=["POST"])
@api_login_required
def api_import_grades():

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
            logger.error("Error creating LO '%s': %s", lo_name, e)

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

            new_links = []
            for lo_name in extracted_los:
                if not lo_name:
                    continue
                lo_id = lo_map.get(lo_name.strip().lower())
                if lo_id and lo_id not in already_linked:
                    new_links.append({"assignment_id": assignment_id, "learning_objective_id": lo_id})
                    already_linked.add(lo_id)
            if new_links:
                supabase_admin.table("assignment_objectives").insert(new_links).execute()
                logger.info("Batch-linked %d LOs to assignment %s", len(new_links), assignment_id)
        except Exception as e:
            logger.error("Error linking LOs to assignment: %s", e)

    for student in students:
        full_name = (student.get('name') or student.get('full_name') or '').strip()
        logger.debug("Processing student: name='%s', full_name='%s', extracted='%s'", student.get('name'), student.get('full_name'), full_name)
        if not full_name:
            logger.debug("Skipping student - no name found")
            continue

        # Try to find existing profile by name (avoid single() throwing when no rows)
        profile_id = None
        try:
            profile_resp = supabase_admin.table("profiles").select("id").eq("full_name", full_name).limit(1).execute()
            if profile_resp.data and len(profile_resp.data) > 0:
                profile_id = profile_resp.data[0]['id']
                logger.debug("Found existing profile for '%s': %s", full_name, profile_id)
        except Exception as e:
            logger.error("Error searching for profile for '%s': %s", full_name, e)
            profile_id = None

        if not profile_id:
            profile_id = str(uuid4())
            try:
                result = supabase_admin.table("profiles").insert({
                    "id": profile_id,
                    "full_name": full_name,
                    "role": "student"
                }).execute()
                logger.debug("Created new profile for '%s': %s", full_name, profile_id)
            except Exception as e:
                logger.error("Error creating profile for '%s': %s", full_name, e)
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
            logger.error("Error enrolling %s: %s", full_name, e)

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
                "top_score": normalized,
                "assignment_id": assignment_id
            })

        imported += 1

    # Batch upsert grades — overwrites existing scores for the same student+LO+assignment
    try:
        chunk_size = 150
        for i in range(0, len(grade_rows), chunk_size):
            chunk = grade_rows[i:i + chunk_size]
            # Add updated_at to each row so the timestamp refreshes on overwrite
            for row in chunk:
                row["updated_at"] = "now()"
            try:
                supabase_admin.table("grades").upsert(
                    chunk, on_conflict="student_id,learning_objective_id,assignment_id"
                ).execute()
                logger.info("Upserted %d grades", len(chunk))
            except Exception as upsert_err:
                logger.error("Grade upsert error: %s", upsert_err)
    except Exception as e:
        logger.error("Error processing grades in bulk: %s", e)

    return jsonify({"success": True, "imported_students": imported, "imported_grades": len(grade_rows)}), 200


@main_bp.route("/api/analyze-grade-pdf", methods=["POST"])
@api_login_required
def analyze_grade_pdf():
    """
    Analyze a grade sheet (PDF or JPG) using Gemini Vision.
    Returns extracted student data and learning objectives.
    Works with both printed and handwritten grade sheets.
    
    Returns:
        JSON with:
        - success: bool
        - data: {students, learning_objectives, raw_text}
        - error: str (if failed)
    """
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
        
        # Initialize Gemini Analyzer
        logger.info("Getting Gemini analyzer...")
        analyzer = get_gemini_analyzer()
        logger.info("Analyzer ready: %s", analyzer is not None)
        
        if analyzer is None:
            return jsonify({
                "success": False,
                "error": "Gemini not properly configured. Set GEMINI_API_KEY and install: pip install google-genai"
            }), 500
        
        # Read PDF content
        pdf_content = pdf_file.read()
        
        # Analyze PDF with Gemini
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
        logger.error("Error analyzing PDF: %s", e)
        return jsonify({
            "success": False,
            "error": f"Failed to analyze PDF: {str(e)}"
        }), 500
