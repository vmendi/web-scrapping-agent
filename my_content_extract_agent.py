from typing import List, Dict, Any
import asyncio
import datetime
import json
import logging
import os
import csv
from dataclasses import dataclass

from openai import OpenAI
from browser_use.browser.context import BrowserContext
import markdownify
import my_utils

logger = logging.getLogger(__name__)


class MyContentExtractAgent:
    """An autonomous agent that extracts structured content from a web page.

    The agent follows a very similar pattern to `MyNavigatorAgent`, but it is purpose‑built
    for a single task: Understand the extraction goal, and return a table of
    rows that comply with a JSON schema provided by the caller.

    Args:
    browser_context: BrowserContext
        A Playwright/BrowserUse browser context that the agent will use to load pages.
    openai_client: OpenAI
        An `openai.OpenAI` client.
    extraction_goal: str
        A natural language description of what information should be extracted.
    row_schema: str
        A JSON schema describing the *shape of a single row* of the table that should be
        extracted. The agent will produce a JSON array where each element satisfies this
        schema.
    """
    def __init__(self, ctx: my_utils.MyAgentContext, extraction_goal: str, row_schema: str):
        self.max_steps = 10
        self.ctx = ctx
        self.extraction_goal = extraction_goal
        self.output_schema = my_utils.convert_simplified_schema_to_openai_output_schema(row_schema)

        # Messages
        self.message_manager = my_utils.MessageManager(system_message_content=self._read_system_prompt())

        # First user prompt (named `extraction_goal` in spec)
        self.message_manager.add_user_message(
            content=("You are tasked with extracting structured data from a webpage.\n"
                     f"Extraction goal: {self.extraction_goal}\n\n"
                     "The caller provided the JSON schema of a *single row* that must be adhered to:\n"
                     f"```json\n{json.dumps(row_schema, indent=2)}\n```\n"
                     "Produce a JSON array where each element respects that schema. Only output JSON. No additional text.")
        )

    def _read_system_prompt(self) -> str:
        with open( "my_content_extract_system_prompt_00.md", "r", encoding="utf-8") as fh:
            return fh.read()
        
    async def run(self) -> tuple[list[dict], str]:
        """Execute the extraction workflow and return the resulting rows.

        Returns
        -------
        rows: list[dict[str, Any]]
            The extracted rows.
        csv_path: str
            The absolute path of the CSV file where the rows were persisted.
        """
        page = await self.ctx.browser_context.get_current_page()
        await page.wait_for_load_state()

        # 2. Get page content (HTML → Markdown to make it easier for the LLM)
        html = await page.content()
        markdown_content = markdownify.markdownify(html)

        # 3. Add page content as user input
        self.message_manager.add_user_message(
            content=("Here is the full page content rendered as Markdown. Perform the extraction according to the goal and schema described earlier.\n\n"
                     f"```markdown\n{markdown_content}\n```")
        )

        # 4. Call the LLM (single shot)
        messages = self.message_manager.get_messages()
        
        logger.info("Sending extraction prompt to OpenAI – input token count ≄ %s", len(str(messages)))
        response = self.ctx.openai_client.responses.create(
            model="o4-mini",
            input=messages,
            text=self.output_schema,
            tools=[],
            store=False,
        )

        if not response.output_text:
            raise RuntimeError("LLM did not return any `output_text`.  Cannot proceed with extraction.")

        try:
            response_data = json.loads(response.output_text)
            if not isinstance(response_data, dict) or "rows" not in response_data:
                raise ValueError("Expected a JSON object with a 'rows' array, got something else.")
            rows: list[dict[str, Any]] = response_data["rows"]
            if not isinstance(rows, list):
                raise ValueError("Expected a JSON array of rows, got something else.")
        except Exception as err:
            logger.error("Failed to parse LLM output as JSON: %s", err)
            raise

        # 5. Persist to CSV
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"{self.ctx.save_dir}/extracted_{timestamp}.csv"        
        csv_path = os.path.abspath(csv_filename)
        try:
            if rows:
                fieldnames = list(rows[0].keys())
                with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
        except Exception as err:
            logger.error("Failed to persist extracted rows to CSV: %s", err)
            raise

        return rows, csv_path
    