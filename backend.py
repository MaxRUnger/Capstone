# create student objects that have learning objectives objects. Learning objectives have grades (and a number of masteries required). 
# grades should be used to return feedback. I think feedback should be a function that takes grades and returns
# feedback based on the number of required grades and the highest grades

import random
from typing import List

# --- Data Structure Classes ---

class Grade:
    """Represents a single grade for a learning objective."""
    
    def __init__(self, mark: str, priority: int, student_id: int):
        """
        Initializes a Grade object.
        
        :param masteries: simple list of possible marks
        with error checking. 
        """
        self.priority = priority
        self.mark = mark
        self.student_id = student_id

        # each M, R, RQ, P, X, or A get a number to help sort them
        # marks that are mistyped get a -1 priority
        if mark == "M":
            self.priority = 5
        elif mark == "R":
            self.priority = 4
        elif mark == "RQ":
            self.priority = 3
        elif mark == "P":
            self.priority = 2
        elif mark == "X":
            self.priority = 1
        elif mark == "A":
            self.priority = 0
        else :
            self.priority = -1 
        
        # add error checking to ensure that the responces are M, R, RQ, P, X, or A
    
    def __repr__(self):
        """String representation of the Grade object."""
        # Display the name and list of masteries 
        return (f"Grade(name='{self.mark}'),"
                    f"priority={self.priority}")
    

# ---

class LearningObjective:
    """Represents a single learning goal with one or more associated grades."""
    
    def __init__(self, objective_id: str, masteriesRequired: int, student_id: int):
        """
        Initializes a LearningObjective object.
        
        :param objective_id: A unique identifier for the objective (e.g., 'LO_001').
        :param masteriesRequired: number of needed Ms needed to count the Learning 
        objective as mastered.
        """
        self.objective_id = objective_id
        self.masteriesRequired = masteriesRequired
        self.student_id = student_id
        self.grades: List[Grade] = []

    def add_grade(self, grade: Grade):
        """Adds a Grade object to this learning objective."""
        self.grades.append(grade)

    #add function to, based on the number of masteries required, return the best current grade.
    #must include contingency for if the number of current grades is lower than the number
    #of masteriesRequired

    #def sort_grades(self):
        #grades.sort(key=lambda x: x.priority, reversed=True)


    def best_grades(self) -> List[Grade]:
        """
        Returns a list of the highest-priority grades, up to the number
        specified by masteriesRequired.
        
        If the total number of grades is less than masteriesRequired,
        it returns all available grades, sorted by priority.
        """
        if not self.grades:
            return []

        # 1. Sort the grades by priority in descending order (M is highest)
        # We use a lambda function to sort based on the 'priority' attribute
        sorted_grades = sorted(self.grades, key=lambda grade: grade.priority, reverse=True)
        
        # 2. Return the top N grades, where N is masteriesRequired
        # This handles the contingency: if there are fewer than N grades, 
        # it just returns all of them.
        return sorted_grades[:self.masteriesRequired]
    
    def get_feedback_message(self) -> List[str]:
        """
        Analyzes the top grades and returns structured feedback messages.
        
        Feedback includes:
        1. A mastery status message.
        2. Unique messages for each distinct letter grade in the top N grades.
        """
        feedback_messages = []
        top_grades = self.best_grades()
        
        # Get the marks from the top grades
        top_marks = [grade.mark for grade in top_grades]
        
        # Determine unique marks for non-duplicating feedback
        unique_marks = set(top_marks)
        
        # 1. Mastery Status Message
        m_count = top_marks.count('M')
        if m_count >= self.masteriesRequired:
            feedback_messages.append(" **Congratulations, all Ms needed have been earned!** This objective is mastered.")
        else:
            required_count = self.masteriesRequired - m_count
            feedback_messages.append(f" You need **{required_count}** more 'M' marks to master this learning objective (requires {self.masteriesRequired} total 'M's).")

        # 2. Grade-Specific Feedback Messages

        grade_messages = {
            'M': "Mastered (M): You've demonstrated full understanding and proficiency in this area.",
            'R': "Revise For Mastery (R): Your work shows a strong grasp of the concept, but you made a non-fundamental error. You can revise this into a mastery "
            "by fixing the error and returning to your professor with that correction.",
            'RQ': "Revise with additional Question for Mastery (RQ): You are close, but made a small fundamental error. You can revise this into a mastery "
            "by reveiwing the topic and asking your professor for a new, similar question and then answering that new question.",
            'P': "Progressing (P): There is some relevant work, but that work has a fundamental error or misunderstanding. Or, that work"
            "shows promise but is incomplete. You can ask your professor for an Additional Mastery Opportunity (AMO) to revise this P into a M.",
            'X': "No Evidence (X): There was not enough work to determine mastery or that work was unclear or inconsistant. You can ask your "
            "professor for an Additional Mastery Opportunity (AMO) to revise this X into a M.",
            'A': "Absent (A): This mark suggests a grade was missed due to absence. Please follow up to complete the work."
        }

        for mark in ['M', 'R', 'RQ', 'P', 'X', 'A']: # Process in priority order
            if mark in unique_marks:
                feedback_messages.append(f"* {grade_messages.get(mark, f'Unknown grade ({mark}) found.')}")
                
        return feedback_messages



    def __repr__(self):
        """String representation of the LearningObjective object."""
        return (f"LearningObjective(id='{self.objective_id}', "
                f"grades_count={len(self.grades)}),"
                f"masteriesNeeded={self.masteriesRequired}")

# ---

class Student:
    """Represents a student who has multiple learning objectives."""
    
    def __init__(self, student_id: int, name: str):
        """
        Initializes a Student object.
        
        :param student_id: The student's unique ID.
        :param name: The student's full name.
        """
        self.student_id = student_id
        self.name = name
        self.learning_objectives: List[LearningObjective] = []
        
    def add_objective(self, objective: LearningObjective):
        """Adds a LearningObjective object to the student's profile."""
        self.learning_objectives.append(objective)
        
    def __repr__(self):
        """String representation of the Student object."""
        return (f"Student(id={self.student_id}, name='{self.name}', "
                f"objectives_count={len(self.learning_objectives)})")

def main():
    # =================================================================
    # --- Instance Code: Creating and Testing Data ---
    # =================================================================
    """
    print("--- Initializing System ---")

    # 1. Create a Student
    student_sarah = Student(student_id=4002, name="Sarah Connor")
    print(student_sarah)
    print("-" * 30)
    """

    student1 = Student(student_id=4002, name="Testy McTestface")        
    # 2. Create Learning Objectives

    # LO A: Requires 2 'M' marks for mastery
    lo_concept1 = LearningObjective("Concept001", masteriesRequired=2, student_id=4002)
    # LO B: Requires 3 'M' marks for mastery
    lo_concept2 = LearningObjective("Concept002", masteriesRequired=3, student_id=4002)  

    # 3. Add Grades to Objectives

    # --- Scenario 1: LO A (Mastered) ---
    # Sarah's marks for Algebra: M, P, M. (2 'M's achieved)
    lo_concept1.add_grade(Grade('M',0,4002))
    lo_concept1.add_grade(Grade('P',0,4002))
    lo_concept1.add_grade(Grade('M',0,4002))
    student1.add_objective(lo_concept1)    

    # --- Scenario 2: LO B (In Progress/Needs Work) ---
    # Sarah's marks for Calculus: R, RQ, P, R. (0 'M's achieved)
    lo_concept2.add_grade(Grade('R',0,4002))
    lo_concept2.add_grade(Grade('RQ',0,4002))
    lo_concept2.add_grade(Grade('P',0,4002))
    lo_concept2.add_grade(Grade('R',0,4002))
    student1.add_objective(lo_concept2)

    print(f"--- Student Grade Summary for {student1.name} ---")
    
    for objective in student1.learning_objectives:
        print(f"\n## Objective: {objective.objective_id}")
        print(f"Masteries Required: **{objective.masteriesRequired}**")
        
        # Get the top N grades
        best_n_grades = objective.best_grades()
        
        # Extract the marks for easy viewing
        best_marks = [grade.mark for grade in best_n_grades]
        
        # Extract all marks for comparison
        all_marks = [grade.mark for grade in objective.grades]

        print(f"All Marks Received (Total: {len(all_marks)}): {', '.join(all_marks)}")
        print(f"**Top {objective.masteriesRequired} Grades:** {', '.join(best_marks)}")

        feedback = objective.get_feedback_message()
        for line in feedback:
            print(line)



if __name__=="__main__":
    main()
