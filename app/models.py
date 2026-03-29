from app.authentication import supabase, supabase_admin

class Profile:
    @staticmethod
    def get_by_id(user_id):
        """Fetches a single user profile by their UUID."""
        response = supabase_admin.table("profiles").select("*").eq("id", user_id).single().execute()
        return response.data

class Course:
    @staticmethod
    def get_all_for_instructor(instructor_id):
        """Fetches all classes taught by a specific instructor."""
        response = supabase_admin.table("classes").select("*").eq("instructor_id", instructor_id).execute()
        return response.data

    @staticmethod
    def get_full_class_data(class_id):
        """Fetches a class, its learning objectives, and all enrolled students with their grades."""
        try:
            # Query class with basic data
            response = supabase_admin.table("classes").select(
                "id, name, semester, learning_objectives(id, name, vendor_code, required_ms)"
            ).eq("id", class_id).execute()

            if not response.data or len(response.data) == 0:
                return None

            class_data = response.data[0]

            # Fetch auto-convert and mastery settings separately so a missing
            # column doesn't break the entire class load.
            try:
                ac_resp = supabase_admin.table("classes").select(
                    "auto_convert_m, min_masteries, num_learning_objectives"
                ).eq("id", class_id).execute()
                if ac_resp.data:
                    class_data.update(ac_resp.data[0])
            except Exception:
                class_data.setdefault('auto_convert_m', False)
                class_data.setdefault('min_masteries', 2)
                class_data.setdefault('num_learning_objectives', 0)

            # Fetch hw_passes fields separately so a missing column doesn't
            # break the entire class load.
            try:
                hp_resp = supabase_admin.table("classes").select(
                    "hw_passes_enabled, hw_passes_allowed"
                ).eq("id", class_id).execute()
                if hp_resp.data:
                    class_data.update(hp_resp.data[0])
            except Exception:
                class_data.setdefault('hw_passes_enabled', False)
                class_data.setdefault('hw_passes_allowed', 2)

            # Fetch enrollments with profiles separately
            try:
                enrollments_resp = supabase_admin.table("enrollments").select(
                    "id, class_id, student_id, muted, profiles(id, full_name, role)"
                ).eq("class_id", class_id).execute()
                enrollments = enrollments_resp.data or []
            except Exception:
                # Fallback if 'muted' column doesn't exist yet
                enrollments_resp = supabase_admin.table("enrollments").select(
                    "id, class_id, student_id, profiles(id, full_name, role)"
                ).eq("class_id", class_id).execute()
                enrollments = enrollments_resp.data or []
                for e in enrollments:
                    e['muted'] = False
            
            # For each enrollment, fetch the student's grades
            for enrollment in enrollments:
                profile = enrollment.get('profiles')
                if profile:
                    student_id = profile.get('id')
                    if student_id:
                        try:
                            grades_resp = supabase_admin.table("grades").select(
                                "learning_objective_id, top_score, second_score, learning_objectives(id, name, required_ms)"
                            ).eq("student_id", student_id).execute()
                            profile['grades'] = grades_resp.data or []
                        except Exception as e:
                            print(f"Error loading grades for student {student_id}: {e}")
                            profile['grades'] = []
            
            class_data['enrollments'] = enrollments
            return class_data
            
        except Exception as e:
            import traceback
            print(f"Database error in get_full_class_data: {e}")
            traceback.print_exc()
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
    def update_score(student_id, lo_id, top_score, second_score=None):
        """Upserts a grade for a student and a specific learning objective.

        The database enforces that scores are one of the mastery codes (e.g. M, R, P, X).
        When we receive numeric scores (e.g. from OCR), we map them to a mastery code
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

        # Ensure `upsert` updates existing grades instead of throwing on duplicates.
        # Supabase requires specifying the conflict target for proper behavior.
        return supabase_admin.table("grades").upsert(data, on_conflict="student_id,learning_objective_id").execute()

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
    @staticmethod
    def get_student_scores(student_id, class_id):
        """Fetches homework performance for a specific student in a class."""
        response = supabase_admin.table("homework_scores").select("*")\
            .eq("student_id", student_id)\
            .eq("class_id", class_id).execute()
        return response.data