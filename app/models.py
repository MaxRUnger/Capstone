from app.authentication import supabase

class Profile:
    @staticmethod
    def get_by_id(user_id):
        """Fetches a single user profile by their UUID."""
        response = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
        return response.data

class Course:
    @staticmethod
    def get_all_for_instructor(instructor_id):
        """Fetches all classes taught by a specific instructor."""
        response = supabase.table("classes").select("*").eq("instructor_id", instructor_id).execute()
        return response.data

    @staticmethod
    def get_full_class_data(class_id):
        """Fetches a class, its learning objectives, and all enrolled students with their grades."""
        try:
            # Removed 'number' from the select string to stop the 'column does not exist' error
            response = supabase.table("classes").select("""
                id, name, semester,
                learning_objectives(id, name),
                enrollments(
                    profiles(id, full_name, role, 
                        grades(learning_objective_id, top_score, second_score)
                    )
                )
            """).eq("id", class_id).execute()

            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
            
        except Exception as e:
            # This is where your error message was printed
            print(f"Database error in get_full_class_data: {e}")
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
    def update_score(student_id, lo_id, top_score, second_score=None):
        """Upserts a grade for a student and a specific learning objective."""
        data = {
            "student_id": student_id,
            "learning_objective_id": lo_id,
            "top_score": top_score
        }
        if second_score:
            data["second_score"] = second_score
            
        return supabase.table("grades").upsert(data).execute()

class Student:
    @staticmethod
    def get_dashboard_data(student_id):
        """Fetches a student's profile and grades for the dashboard."""
        response = supabase.table("profiles").select("""
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
        response = supabase.table("homework_scores").select("*")\
            .eq("student_id", student_id)\
            .eq("class_id", class_id).execute()
        return response.data