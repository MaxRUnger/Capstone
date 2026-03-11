from app.authentication import supabase_admin as supabase


class StudentDAO:

    @staticmethod
    def get_all_in_class(class_id: str) -> list:
        """
        Returns a list of students enrolled in a class,
        each with their grades for that class's learning objectives.

        Shape matches what routes.py / templates expect:
        [
          {
            'id': '...',
            'name': '...',
            'learning_objectives': [
              {'name': '...', 'top_score': 'M', 'second_score': 'R'},
              ...
            ]
          },
          ...
        ]
        """
        # 1. Get all learning objectives for this class (for ordering)
        lo_result = (
            supabase.table("learning_objectives")
            .select("id, name, display_order")
            .eq("class_id", class_id)
            .order("display_order")
            .execute()
        )
        lo_map = {lo["id"]: lo["name"] for lo in lo_result.data}
        lo_order = [lo["id"] for lo in lo_result.data]

        # 2. Get enrolled students
        enrollment_result = (
            supabase.table("enrollments")
            .select("student_id, profiles(id, full_name)")
            .eq("class_id", class_id)
            .execute()
        )

        if not enrollment_result.data:
            return []

        student_ids = [row["student_id"] for row in enrollment_result.data]
        student_names = {
            row["student_id"]: row["profiles"]["full_name"]
            for row in enrollment_result.data
            if row.get("profiles")
        }

        # 3. Get all grades for these students in this class's LOs
        if not lo_order:
            # No LOs yet — return students with empty lo lists
            students = [
                {"id": sid, "name": student_names.get(sid, "Unknown"), "learning_objectives": []}
                for sid in student_ids
            ]
            return sorted(students, key=lambda s: s["name"])

        grades_result = (
            supabase.table("grades")
            .select("student_id, learning_objective_id, top_score, second_score")
            .in_("student_id", student_ids)
            .in_("learning_objective_id", lo_order)
            .execute()
        )

        # Build a lookup: grades_lookup[student_id][lo_id] = {top_score, second_score}
        grades_lookup: dict[str, dict[str, dict]] = {}
        for grade in grades_result.data:
            sid = grade["student_id"]
            lid = grade["learning_objective_id"]
            grades_lookup.setdefault(sid, {})[lid] = {
                "top_score": grade["top_score"] or "",
                "second_score": grade["second_score"] or "",
            }

        # 4. Assemble student objects
        students = []
        for sid in student_ids:
            lo_list = []
            for lo_id in lo_order:
                grade = grades_lookup.get(sid, {}).get(lo_id, {"top_score": "", "second_score": ""})
                lo_list.append({
                    "name": lo_map[lo_id],
                    "top_score": grade["top_score"],
                    "second_score": grade["second_score"],
                })
            students.append({
                "id": sid,
                "name": student_names.get(sid, "Unknown"),
                "learning_objectives": lo_list,
            })

        return sorted(students, key=lambda s: s["name"])

    @staticmethod
    def get_by_id_in_class(class_id: str, student_id: str) -> dict | None:
        """Get a single student with their grades for a specific class."""
        all_students = StudentDAO.get_all_in_class(class_id)
        return next((s for s in all_students if s["id"] == student_id), None)

    @staticmethod
    def enroll(class_id: str, student_id: str) -> None:
        """Enroll a student in a class (upsert to avoid duplicates)."""
        supabase.table("enrollments").upsert({
            "class_id": class_id,
            "student_id": student_id,
        }).execute()

    @staticmethod
    def unenroll(class_id: str, student_id: str) -> None:
        (
            supabase.table("enrollments")
            .delete()
            .eq("class_id", class_id)
            .eq("student_id", student_id)
            .execute()
        )

    @staticmethod
    def get_enrolled_class(student_id: str) -> dict | None:
        """
        Get the class a student is enrolled in (assumes one class per student for now).
        Returns class dict or None.
        """
        result = (
            supabase.table("enrollments")
            .select("class_id, classes(id, name, semester)")
            .eq("student_id", student_id)
            .limit(1)
            .execute()
        )
        if result.data and result.data[0].get("classes"):
            return result.data[0]["classes"]
        return None
