from app.authentication import supabase


class GradeDAO:

    @staticmethod
    def upsert(student_id: str, learning_objective_id: str,
               top_score: str, second_score: str) -> dict:
        """
        Insert or update a grade for a student on a specific LO.
        Uses upsert on the unique(student_id, learning_objective_id) constraint.
        """
        result = (
            supabase.table("grades")
            .upsert({
                "student_id": student_id,
                "learning_objective_id": learning_objective_id,
                "top_score": top_score,
                "second_score": second_score,
                "updated_at": "now()",
            }, on_conflict="student_id,learning_objective_id")
            .execute()
        )
        return result.data[0]

    @staticmethod
    def upsert_bulk(grades: list[dict]) -> list:
        """
        Upsert many grades at once.
        Each dict: {student_id, learning_objective_id, top_score, second_score}
        """
        rows = [
            {
                "student_id": g["student_id"],
                "learning_objective_id": g["learning_objective_id"],
                "top_score": g.get("top_score"),
                "second_score": g.get("second_score"),
                "updated_at": "now()",
            }
            for g in grades
        ]
        result = (
            supabase.table("grades")
            .upsert(rows, on_conflict="student_id,learning_objective_id")
            .execute()
        )
        return result.data

    @staticmethod
    def get_for_student_in_class(student_id: str, class_id: str) -> list:
        """Get all grades for a student in a specific class."""
        result = (
            supabase.table("grades")
            .select("*, learning_objectives(id, name, display_order)")
            .eq("student_id", student_id)
            .execute()
        )
        # Filter to only LOs belonging to this class
        return [
            g for g in result.data
            if g.get("learning_objectives", {}).get("class_id") == class_id
        ]

    @staticmethod
    def get_lo_id_by_name(class_id: str, lo_name: str) -> str | None:
        """Look up a learning objective ID by its name and class."""
        result = (
            supabase.table("learning_objectives")
            .select("id")
            .eq("class_id", class_id)
            .eq("name", lo_name)
            .single()
            .execute()
        )
        return result.data["id"] if result.data else None

    @staticmethod
    def delete(student_id: str, learning_objective_id: str) -> None:
        (
            supabase.table("grades")
            .delete()
            .eq("student_id", student_id)
            .eq("learning_objective_id", learning_objective_id)
            .execute()
        )
