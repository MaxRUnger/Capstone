import logging
from datetime import date

from app.authentication import supabase, supabase_admin

logger = logging.getLogger(__name__)

# Valid mastery grade codes used throughout the grading system
MASTERY_GRADES = ('M', 'R', 'RQ', 'P', 'X', 'A')


class Course:
    @staticmethod
    def get_all_for_instructor(instructor_id):
        """Fetches all classes taught by a specific instructor."""
        response = supabase_admin.table("classes").select("*").eq("instructor_id", instructor_id).execute()
        return response.data

    @staticmethod
    def get_lo_ids_for_class(class_id):
        """Return a list of learning-objective IDs belonging to this class."""
        resp = supabase_admin.table("learning_objectives").select("id").eq("class_id", class_id).execute()
        return [lo['id'] for lo in (resp.data or []) if lo.get('id')]

    @staticmethod
    def get_learning_objectives(class_id):
        """Return learning objectives for a class (includes fields needed for edit UI)."""
        resp = supabase_admin.table("learning_objectives") \
            .select("id, name, vendor_code, description, required_ms") \
            .eq("class_id", class_id) \
            .execute()
        return resp.data or []

    @staticmethod
    def get_full_class_data(class_id):
        """Fetches a class, its learning objectives, and all enrolled students with their grades."""
        try:
            response = supabase_admin.table("classes").select(
                "id, name, semester, learning_objectives(id, name, vendor_code, required_ms)"
            ).eq("id", class_id).execute()

            if not response.data or len(response.data) == 0:
                return None

            class_data = response.data[0]
            class_lo_ids = {
                str(lo.get("id"))
                for lo in (class_data.get("learning_objectives") or [])
                if lo.get("id")
            }

            # Fetch optional class-level settings in a single query.
            # If any column doesn't exist yet, fall back to defaults.
            try:
                settings_resp = supabase_admin.table("classes").select(
                    "auto_convert_m, min_masteries, num_learning_objectives, "
                    "hw_passes_enabled, hw_passes_allowed, is_online"
                ).eq("id", class_id).execute()
                if settings_resp.data:
                    class_data.update(settings_resp.data[0])
            except Exception:
                class_data.setdefault('auto_convert_m', False)
                class_data.setdefault('min_masteries', 2)
                class_data.setdefault('num_learning_objectives', 0)
                class_data.setdefault('hw_passes_enabled', False)
                class_data.setdefault('hw_passes_allowed', 2)
                class_data.setdefault('is_online', False)
            class_data.setdefault('is_online', False)

            # Fetch enrollments with profiles (email column optional until DB migration applied)
            try:
                enrollments_resp = supabase_admin.table("enrollments").select(
                    "id, class_id, student_id, muted, profiles(id, full_name, role, email)"
                ).eq("class_id", class_id).execute()
                enrollments = enrollments_resp.data or []
            except Exception:
                try:
                    enrollments_resp = supabase_admin.table("enrollments").select(
                        "id, class_id, student_id, muted, profiles(id, full_name, role)"
                    ).eq("class_id", class_id).execute()
                    enrollments = enrollments_resp.data or []
                except Exception:
                    enrollments_resp = supabase_admin.table("enrollments").select(
                        "id, class_id, student_id, profiles(id, full_name, role)"
                    ).eq("class_id", class_id).execute()
                    enrollments = enrollments_resp.data or []
                    for e in enrollments:
                        e['muted'] = False

            # Batch-fetch grades for ALL enrolled students in one query (fixes N+1)
            student_ids = [
                e['profiles']['id']
                for e in enrollments
                if isinstance(e.get('profiles'), dict) and e['profiles'].get('id')
            ]
            grades_by_student = {}
            if student_ids:
                try:
                    grades_resp = supabase_admin.table("grades").select(
                        "student_id, learning_objective_id, top_score, second_score, "
                        "learning_objectives(id, name, vendor_code, required_ms)"
                    ).in_("student_id", student_ids).execute()
                    for g in (grades_resp.data or []):
                        lo_gid = g.get("learning_objective_id")
                        if class_lo_ids and str(lo_gid) not in class_lo_ids:
                            continue
                        grades_by_student.setdefault(g["student_id"], []).append(g)
                except Exception as e:
                    logger.error("Error batch-loading grades: %s", e)

            for enrollment in enrollments:
                profile = enrollment.get('profiles')
                if isinstance(profile, dict) and profile.get('id'):
                    profile['grades'] = grades_by_student.get(profile['id'], [])

            class_data['enrollments'] = enrollments
            return class_data

        except Exception as e:
            logger.error("Database error in get_full_class_data: %s", e, exc_info=True)
            return None


class Grade:
    @staticmethod
    def get_priority(mark):
        """Maps letter grades to numerical priorities."""
        priorities = {'M': 5, 'R': 4, 'RQ': 3, 'P': 2, 'X': 1, 'A': 0}
        return priorities.get(mark, -1)

    @staticmethod
    def is_mastered(top_score, second_score):
        """Logic to determine if an objective is mastered (Two 'M's)."""
        return top_score == 'M' and second_score == 'M'

    @staticmethod
    def normalize_score(score):
        """Normalize an incoming grade value to the allowed mastery codes.

        Accepts numeric scores (e.g. 82, 99.2) and converts them to a mastery band.
        Also accepts already-normalized values like 'M', 'P', 'X', or 'R'.
        """
        if score is None:
            return None

        if isinstance(score, (int, float)) or (isinstance(score, str) and score.replace('.', '', 1).isdigit()):
            try:
                val = float(score)
                # Simple mapping: high values become Mastered, else needs review
                if val >= 70:
                    return 'M'
                if val >= 50:
                    return 'R'
                return 'P'
            except Exception:
                pass

        # Otherwise assume it's already one of the allowed codes
        if isinstance(score, str):
            score = score.strip().upper()
            if score in ('M', 'R', 'RQ', 'P', 'X', 'A'):
                return score

        return None

    @staticmethod
    def update_score(student_id, lo_id, top_score, second_score=None, assignment_id=None):
        """Upserts a grade for a student, learning objective, and assignment.

        The database enforces that scores are one of the mastery codes (e.g. M, R, P, X).
        When we receive numeric scores (e.g. from Gemini), we map them to a mastery code
        so the import pipeline doesn't fail due to check constraints.
        """

        normalized_top = Grade.normalize_score(top_score)
        normalized_second = Grade.normalize_score(second_score)

        data = {
            "student_id": student_id,
            "learning_objective_id": lo_id,
            "top_score": normalized_top
        }
        if normalized_second is not None:
            data["second_score"] = normalized_second
        if assignment_id is not None:
            data["assignment_id"] = assignment_id

        # Ensure `upsert` updates existing grades instead of throwing on duplicates.
        # Supabase requires specifying the conflict target for proper behavior.
        return supabase_admin.table("grades").upsert(data, on_conflict="student_id,learning_objective_id,assignment_id").execute()

    @staticmethod
    def get_overdue_revisions(class_id):
        """Return RQ grades where the assignment's revision_due date has passed
        and the student was eligible for revision (HW >= 65% or used a pass)."""
        today = date.today().isoformat()
        try:
            lo_ids = Course.get_lo_ids_for_class(class_id)
            if not lo_ids:
                return []
            resp = supabase_admin.table("grades").select(
                "student_id, top_score, assignment_id, "
                "assignments(id, name, revision_due), "
                "learning_objectives(id, name, vendor_code)"
            ).eq("top_score", "RQ").in_("learning_objective_id", lo_ids).execute()

            # Collect assignment IDs to batch-check eligibility
            assignment_ids = set()
            for g in (resp.data or []):
                aid = g.get('assignment_id')
                if aid:
                    assignment_ids.add(aid)

            # Batch-fetch HW scores for all relevant assignments
            eligibility_by_assignment = {}
            for aid in assignment_ids:
                eligibility_by_assignment[aid] = Homework.get_revision_eligibility(class_id, aid)

            overdue = []
            for g in (resp.data or []):
                assignment = g.get('assignments') or {}
                rev_due = assignment.get('revision_due')
                if not rev_due or rev_due >= today:
                    continue
                # Only flag if student was eligible for revision
                aid = g.get('assignment_id')
                eligible = eligibility_by_assignment.get(aid, {}).get(g['student_id'], False)
                if not eligible:
                    continue
                lo = g.get('learning_objectives') or {}
                overdue.append({
                    'student_id': g['student_id'],
                    'assignment_name': assignment.get('name', ''),
                    'revision_due': rev_due,
                    'lo_name': lo.get('vendor_code') or lo.get('name', 'Unknown LO'),
                })
            return overdue
        except Exception as e:
            logger.error("Error fetching overdue revisions: %s", e)
            return []

class Student:
    @staticmethod
    def get_dashboard_data(student_id):
        """Fetches a student's profile and grades for the dashboard."""
        response = supabase_admin.table("profiles").select("""
            *,
            grades(*, learning_objectives(*)),
            enrollments(
                classes(*)
            )
        """).eq("id", student_id).single().execute()
        return response.data

class Homework:
    REVISION_THRESHOLD = 65

    @staticmethod
    def resolve_hw_group_storage_key(class_id, assignment_id):
        """Return the value stored in homework_scores.homework_group for this assignment.

        All assignments that share the same homework_group label use one HW % row per student.
        If the assignment has no group label, fall back to assignment_id (legacy behavior).
        """
        try:
            resp = supabase_admin.table("assignments").select("homework_group").eq(
                "id", assignment_id
            ).eq("class_id", class_id).limit(1).execute()
            if not resp.data:
                return None
            hg = (resp.data[0].get("homework_group") or "").strip()
            return hg if hg else assignment_id
        except Exception as e:
            logger.error("Error resolving homework group for assignment %s: %s", assignment_id, e)
            return None

    @staticmethod
    def get_hw_scores_map_for_assignment(class_id, assignment_id):
        """Return {student_id: score_pct} for the HW group this assignment belongs to.

        Scores are keyed by the shared homework_group string. Rows keyed by assignment UUID
        (older data) are merged in so every assignment in the group shows the same HW %.
        """
        try:
            resp = supabase_admin.table("assignments").select("id, homework_group").eq(
                "id", assignment_id
            ).eq("class_id", class_id).limit(1).execute()
            if not resp.data:
                return {}
            row = resp.data[0]
            hg = (row.get("homework_group") or "").strip()
            hw_map = {}
            if hg:
                sib = supabase_admin.table("assignments").select("id").eq(
                    "class_id", class_id
                ).eq("homework_group", hg).execute()
                sib_ids = [r["id"] for r in (sib.data or [])]
                if sib_ids:
                    leg = supabase_admin.table("homework_scores").select(
                        "student_id, score_pct"
                    ).eq("class_id", class_id).in_("homework_group", sib_ids).execute()
                    for r in (leg.data or []):
                        hw_map[r["student_id"]] = r["score_pct"]
                cur = supabase_admin.table("homework_scores").select(
                    "student_id, score_pct"
                ).eq("class_id", class_id).eq("homework_group", hg).execute()
                for r in (cur.data or []):
                    hw_map[r["student_id"]] = r["score_pct"]
            else:
                single = supabase_admin.table("homework_scores").select(
                    "student_id, score_pct"
                ).eq("class_id", class_id).eq("homework_group", assignment_id).execute()
                for r in (single.data or []):
                    hw_map[r["student_id"]] = r["score_pct"]
            return hw_map
        except Exception as e:
            logger.error("Error loading homework scores for assignment %s: %s", assignment_id, e)
            return {}

    @staticmethod
    def get_student_scores(student_id, class_id):
        """Fetches homework performance for a specific student in a class."""
        response = supabase_admin.table("homework_scores").select("*")\
            .eq("student_id", student_id)\
            .eq("class_id", class_id).execute()
        return response.data

    @staticmethod
    def get_revision_eligibility(class_id, assignment_id):
        """Return {student_id: bool} indicating revision eligibility for an assignment.

        A student is eligible if their HW score >= 65% or they used a HW pass (score_pct = -1).
        Uses the shared homework-group HW % when the assignment is part of a group.
        """
        try:
            hw_map = Homework.get_hw_scores_map_for_assignment(class_id, assignment_id)
            eligibility = {}
            for sid, score in hw_map.items():
                eligible = score == -1 or (score is not None and score >= Homework.REVISION_THRESHOLD)
                eligibility[sid] = eligible
            return eligibility
        except Exception as e:
            logger.error("Error checking revision eligibility: %s", e)
            return {}