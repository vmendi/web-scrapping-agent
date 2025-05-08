Your task is to extract the courses available to students at all of Harvard University for the academic year of 2024-25. Take into account that Harvard University is comprised of many schools. Make sure to include all schools that offer courses.

**Required Information Fields (for final output)**:

Course Title: The full name or title of the course. (Type: string)
Course Code: The official identifier for the course (e.g., "COMPSCI 50", "HIST 1010"). (Type: string)
Course Description: A description or abstract of the course content. (Type: string)
Department Name: The name of the academic department offering the course. (Type: string)
Department Code: The standardized short code or abbreviation that Harvard assigns to a department and that typically prefixes its course numbers (e.g., COMPSCI, HIST, GOV). (Type: string)
School Name: The Harvard school or college that administratively houses the department and officially offers the course (e.g., Harvard College (FAS), Harvard Business School, Harvard Medical School). (Type: string)

- Example:
{
"course_title": "Introduction to Computer Science",
"course_code": "COMPSCI 50",
"course_description": "An introduction to the intellectual enterprises of computer science",
"department_name": "Computer Science",
"department_code": "COMPSCI",
"school_name": "Harvard College (FAS)"
}