Generate a navigation and location plan (for the WNA agent) to fulfill the following data extraction objective:

**Objective:** Identify and locate the list of primary academic schools associated with Harvard University.

**Target Entity:** Harvard University.

**Required Information Fields (for final output):**
1.  `School Name`: The official name of the school (e.g., "Harvard College", "Harvard Law School"). (Type: string)
2.  `School Website URL`: The main homepage URL for the specific school. (Type: string)

**Constraint:** The plan should guide the WNA to find the main, degree-granting schools. It should aim to exclude individual departments, research centers, or affiliated institutions unless they function as primary schools within the university structure (typically found under main "Academics" or "Schools" listings).