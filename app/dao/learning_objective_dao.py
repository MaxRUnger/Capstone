from app.authentication import supabase


class LearningObjectiveDAO:

    @staticmethod
    def get_all_by_class(class_id: str) -> list:
        """
        Returns LO list with mastery counts already computed.
        Shape expected by templates:
        [
          {
            'id': '...',
            'name': '...',
            'two_m_count': 3,
            'one_m_count': 2,
            'zero_m_count': 1,
            'total_students': 6,
            'students_with_2m': [...],
            'students_with_1m': [...],
            'students_with_0m': [...],
          },
          ...
        ]
        """
        lo_result = (
            supabase.table("learning_objectives")
            .select("id, name, display_order")
            .eq("class_id", class_id)
            .order("display_order")
            .execute()
        )

        if not lo_result.data:
            return []

        lo_ids = [lo["id"] for lo in lo_result.data]

        # Get all grades for these LOs
        grades_result = (
            supabase.table("grades")
            .select("student_id, learning_objective_id, top_score, second_score, profiles(full_name)")
            .in_("learning_objective_id", lo_ids)
            .execute()
        )

        # Count total enrolled students
        enrollment_result = (
            supabase.table("enrollments")
            .select("student_id")
            .eq("class_id", class_id)
            .execute()
        )
        total_students = len(enrollment_result.data)

        # Build per-LO mastery groups
        lo_data: dict[str, dict] = {}
        for lo in lo_result.data:
            lo_data[lo["id"]] = {
                "id": lo["id"],
                "name": lo["name"],
                "total_students": total_students,
                "students_with_2m": [],
                "students_with_1m": [],
                "students_with_0m": [],
            }

        for grade in grades_result.data:
            lo_id = grade["learning_objective_id"]
            if lo_id not in lo_data:
                continue
            m_count = (1 if grade.get("top_score") == "M" else 0) + \
                      (1 if grade.get("second_score") == "M" else 0)
            student_entry = {
                "name": grade["profiles"]["full_name"] if grade.get("profiles") else "Unknown",
                "top_score": grade.get("top_score", ""),
                "second_score": grade.get("second_score", ""),
            }
            if m_count == 2:
                lo_data[lo_id]["students_with_2m"].append(student_entry)
            elif m_count == 1:
                lo_data[lo_id]["students_with_1m"].append(student_entry)
            else:
                lo_data[lo_id]["students_with_0m"].append(student_entry)

        result = []
        for lo in lo_result.data:
            d = lo_data[lo["id"]]
            d["two_m_count"] = len(d["students_with_2m"])
            d["one_m_count"] = len(d["students_with_1m"])
            d["zero_m_count"] = len(d["students_with_0m"])
            result.append(d)

        return result

    @staticmethod
    def get_names_by_class(class_id: str) -> list[str]:
        """Return just the ordered list of LO names (used by speed grader)."""
        result = (
            supabase.table("learning_objectives")
            .select("name")
            .eq("class_id", class_id)
            .order("display_order")
            .execute()
        )
        return [row["name"] for row in result.data]

    @staticmethod
    def create(class_id: str, name: str, display_order: int = 0) -> dict:
        result = (
            supabase.table("learning_objectives")
            .insert({
                "class_id": class_id,
                "name": name,
                "display_order": display_order,
            })
            .execute()
        )
        return result.data[0]

    @staticmethod
    def create_bulk(class_id: str, names: list[str]) -> list:
        """Insert multiple LOs at once (used after CSV/file upload)."""
        rows = [
            {"class_id": class_id, "name": name, "display_order": i}
            for i, name in enumerate(names)
        ]
        result = supabase.table("learning_objectives").insert(rows).execute()
        return result.data

    @staticmethod
    def delete(lo_id: str) -> None:
        supabase.table("learning_objectives").delete().eq("id", lo_id).execute()
