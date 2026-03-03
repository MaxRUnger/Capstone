from app.authentication import supabase


class ClassDAO:

    @staticmethod
    def get_all_by_instructor(instructor_id: str) -> list:
        """Get all classes for an instructor, with student count."""
        result = (
            supabase.table("classes")
            .select("*, enrollments(count)")
            .eq("instructor_id", instructor_id)
            .order("created_at", desc=True)
            .execute()
        )
        classes = {}
        for row in result.data:
            student_count = row.get("enrollments", [{}])[0].get("count", 0) if row.get("enrollments") else 0
            classes[row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "semester": row.get("semester", ""),
                "start_date": row.get("start_date", ""),
                "end_date": row.get("end_date", ""),
                "days": row.get("days", ""),
                "students": [None] * student_count,  # placeholder for count display
            }
        return classes

    @staticmethod
    def get_by_id(class_id: str) -> dict | None:
        """Get a single class by ID."""
        result = (
            supabase.table("classes")
            .select("*")
            .eq("id", class_id)
            .single()
            .execute()
        )
        return result.data

    @staticmethod
    def create(instructor_id: str, name: str, number: str, semester: str,
               start_date: str, end_date: str, days: str) -> dict:
        """Create a new class, returns the created row."""
        full_name = f"{semester} - {number} - {name}" if number and semester else name
        result = (
            supabase.table("classes")
            .insert({
                "instructor_id": instructor_id,
                "name": full_name,
                "number": number,
                "semester": semester,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
            })
            .execute()
        )
        return result.data[0]

    @staticmethod
    def delete(class_id: str) -> None:
        supabase.table("classes").delete().eq("id", class_id).execute()
