{
  "output_schema": [
    {
      "name": "course_title",
      "type": "string",
      "description": "The full name or title of the course."
    },
    {
      "name": "course_code",
      "type": "string",
      "description": "The official identifier for the course."
    },
    {
      "name": "offering_department",
      "type": "string",
      "description": "The academic department or school offering the course."
    },
    {
      "name": "course_description",
      "type": "string",
      "description": "A brief description of the course content."
    },
    {
      "name": "academic_year",
      "type": "string",
      "description": "The academic year the course is listed under (2024-2025)."
    }
  ],
  "plan": [
    {
      "step_id": 1,
      "agent": "WNA",
      "goal": "Perform a web search to find Harvard University's official course catalog or course offerings page for the 2024-2025 academic year.",
      "input_hints": [
        "search: Harvard University course catalog 2024-2025",
        "search: site:harvard.edu \"course catalog\" \"2024-2025\"",
        "look for: Harvard course offerings official 2024/2025 academic year"
      ],
      "output_criteria": "Output the URL(s) most likely to represent the official Harvard course catalog or offerings interface for academic year 2024-2025."
    },
    {
      "step_id": 2,
      "agent": "WNA",
      "goal": "Navigate to the identified URL and verify that it is the authoritative source for Harvard courses for 2024-2025; determine if it's a static listing or a searchable/filterable interface.",
      "input_hints": [
        "look for headings like 'Course Catalog', 'Course Offerings', 'Search Courses'",
        "confirm presence of 'Academic Year 2024-2025' or similar",
        "ensure domain is harvard.edu or an official subdomain"
      ],
      "output_criteria": "Confirm the URL and describe whether the page is a static catalog or offers search/filter functionality for courses."
    },
    {
      "step_id": 3,
      "agent": "WNA",
      "goal": "If the source is a searchable or filterable interface, set or apply the filter to display all courses for academic year 2024-2025; if it is a static page, ensure its content corresponds exclusively to 2024-2025.",
      "input_hints": [
        "look for filters labeled 'Academic Year', 'Year', or 'Term'",
        "set filter to '2024-2025' or equivalent",
        "if necessary, use search inputs to list all courses"
      ],
      "output_criteria": "Provide the resulting URL (including any query parameters) or confirm the static page URL that shows the full list of courses for 2024-2025."
    },
    {
      "step_id": 4,
      "agent": "WNA",
      "goal": "On the page displaying the 2024-2025 course listings, locate the main section or container where individual course entries (code, title, department, description) are presented.",
      "input_hints": [
        "identify repeating patterns for course entries",
        "look for elements containing course codes (e.g., COMPSCI 50), titles, departments, descriptions",
        "locate the section or listing container housing these entries"
      ],
      "output_criteria": "Report the final URL and provide a clear description of the page region (e.g., main content area or listing container) that holds the course entries, enabling the orchestrator to initiate extraction."
    }
  ]
}