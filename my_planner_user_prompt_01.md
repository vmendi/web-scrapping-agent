Generate a navigation and location plan (for the WNA agent) to fulfill the following data extraction objective:

**Objective**: Identify and locate the official source (e.g., course catalog, searchable database) for all courses offered by Harvard University during the 2024/2025 academic year.

**Target Entity**: Harvard University

**Required Information Fields (for final output)**:

Course Title: The full name or title of the course. (Type: string)
Course Code: The official identifier for the course (e.g., "COMPSCI 50", "HIST 1010"). (Type: string)
Offering Department: The name of the academic department or school offering the course. (Type: string)
Course Description: A brief description or abstract of the course content. (Type: string)
Academic Year: The academic year the course is listed under (should be "2024-2025"). (Type: string)
Constraint: The plan must guide the WNA to the primary, authoritative source for Harvard's course listings specifically for the 2024-2025 academic year. The source might be a single comprehensive catalog page, a searchable database interface, or a collection of pages filterable by year/term. Avoid unofficial listings or individual department pages if a central catalog exists.