from typing import List, Dict, Any
import asyncio
import datetime
import json
import logging
import os
import csv
from dataclasses import dataclass

from openai import OpenAI
from openai.types.responses import ResponseFunctionToolCall
from browser_use.browser.context import BrowserContext
from browser_use.browser.views import BrowserState

from my_agent_tools import MyContentExtractAgentTools, ActionResult
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
        self.max_steps = 20
        self.ctx = ctx
        self.agent_id = ctx.generate_next_child_agent_id()
        self.extraction_goal = extraction_goal
        self.output_schema = my_utils.convert_simplified_schema_to_rows_in_openai_output_schema(row_schema)

        self.message_manager = my_utils.MessageManager(system_message_content=self._read_system_prompt())

        self.message_manager.add_user_message(
            content=("You are tasked with extracting structured data from a webpage.\n"
                     f"Extraction goal: {self.extraction_goal}\n\n"
                     "The caller provided the JSON schema of a *single row* that must be adhered to:\n"
                     f"```json\n{json.dumps(row_schema, indent=2)}\n```\n"
                     "Produce a JSON array where each element respects that schema.")
        )

        self.my_agent_tools = MyContentExtractAgentTools(ctx=self.ctx)

    def _read_system_prompt(self) -> str:
        with open( "my_content_extract_system_prompt_00.md", "r", encoding="utf-8") as fh:
            return fh.read()
        
 
 
    async def run(self) -> tuple[list[dict], str]:
        """Main entry-point. Loops through *step* invocations until the agent finishes
        (either by extracting the rows or by reaching the maximum number of steps).

        Returns
        -------
        rows: list[dict[str, Any]]
            The extracted rows.
        csv_path: str
            Absolute path of the CSV file where the rows have been persisted.
        """
        logger.info(f'Starting content-extraction task at {self.ctx.run_id}')

        self._extracted_rows: list[dict[str, Any]] = []
        last_action_result: ActionResult | None = None

        for step_number in range(self.max_steps):
            last_action_result = await self.step(step_number=step_number)

            if last_action_result.is_done:
                break

        if len(self._extracted_rows) > 0:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_path = os.path.abspath(f"{self.ctx.save_dir}/extracted_{timestamp}.csv")

            fieldnames = list(self._extracted_rows[0].keys()) if self._extracted_rows else []
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._extracted_rows)
    
        return self._extracted_rows, self._csv_path

    # ----------------------------------------------------------------------------------
    # Single interaction step with the LLM
    # ----------------------------------------------------------------------------------
    async def step(self, step_number: int) -> ActionResult:
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps)

        browser_state = await self.ctx.browser_context.get_state()
        messages = self.message_manager.get_messages()
        messages.extend(my_utils.get_current_browser_state_message(current_step=step_number, browser_state=browser_state))
        
        page = await self.ctx.browser_context.get_current_page()
        html = await page.content()
        markdown_content = markdownify.markdownify(html)

        messages.append(
            {
                'role': 'user',
                'content': (
                    f'Here is the full page content rendered as Markdown:\n\n'
                    f'```markdown\n{markdown_content}\n```\n\n'
                ),
            }
        )

        my_utils.MessageManager.persist_state(messages=messages, 
                                              step_number=step_number, 
                                              save_dir=f"{self.ctx.save_dir}/{self.agent_id:02d}_content_extract_agent")

        await self.ctx.browser_context.remove_highlights()

        # ------------------------------------------------------------------
        # Invoke the LLM
        # ------------------------------------------------------------------
        logger.info(f'Step {step_number} - sending messages to LLM')

        response = self.ctx.openai_client.responses.create(
            model='o3',
            reasoning={'effort': 'high'},
            input=messages,
            text=self.output_schema,
            tools=self.my_agent_tools.tools_schema,
            tool_choice='auto',
            parallel_tool_calls=False,
            store=False,
            temperature=0.0,
        )

        # ------------------------------------------------------------------
        # Handle the LLM response
        # ------------------------------------------------------------------
        if response.output_text:
            parsed = json.loads(response.output_text)
            self._extracted_rows = parsed['rows']
                        
            action_result = ActionResult(action_result_msg=f'Extraction of {len(self._extracted_rows)} rows completed.', 
                                         success=True, 
                                         is_done=True)
            
            logger.info(f'Step {step_number} - extracted {len(self._extracted_rows)} rows')
        else:
            function_tool_call: ResponseFunctionToolCall | None = next((item for item in response.output if isinstance(item, ResponseFunctionToolCall)), None)

            if not function_tool_call:
                raise RuntimeError('No function tool call detected.')

            self.message_manager.add_ai_function_tool_call_message(function_tool_call=function_tool_call)

            logger.info(f'Step {step_number} - function tool call: {function_tool_call.to_json()}')
            action_result = await self.my_agent_tools.execute_tool(function_tool_call=function_tool_call)
            logger.info(f'Step {step_number} - tool execution result: {action_result.action_result_msg}')

            self.message_manager.add_tool_result_message(result_message=action_result.action_result_msg,
                                                        tool_call_id=function_tool_call.call_id)
            

        return action_result
    