from typing import Any
import datetime
import json
import logging
import os
import csv
from tabulate import tabulate

from openai.types.responses import ResponseFunctionToolCall, Response

from my_agent_tools import MyContentExtractAgentTools, ActionResult
import markdownify
import my_utils

logger = logging.getLogger(__name__)


class MyContentExtractAgent:
    def __init__(self, ctx: my_utils.MyAgentContext, extraction_goal: str, row_schema: str):
        self.max_steps = 20
        self.ctx = ctx
        self.extraction_goal = extraction_goal
        self.output_schema = my_utils.convert_simplified_schema_to_rows_in_openai_output_schema(row_schema)

        self.message_manager = my_utils.MessageManager(system_message_content=self._read_system_prompt())

        self.message_manager.add_user_message(
            content=("You are tasked with extracting structured data from a webpage.\n"
                     f"Extraction goal: {self.extraction_goal}\n\n"
                     "The caller provided the JSON schema of a *single row* that must be adhered to:\n"
                     f"```json\n{json.dumps(row_schema, indent=2)}\n```\n"
                     "Produce a JSON array where each element respects that schema."),
            ephemeral=False
        )

        self.my_agent_tools = MyContentExtractAgentTools(ctx=self.ctx)

    def _read_system_prompt(self) -> str:
        with open( "my_content_extract_system_prompt_00.md", "r", encoding="utf-8") as fh:
            return fh.read()
        
 
    async def run(self) -> ActionResult:
        logger.info(f'Starting content-extraction task at {self.ctx.run_id}')

        last_action_result: ActionResult | None = None
        for step_number in range(self.max_steps):
            last_action_result = await self.step(step_number=step_number)

            if last_action_result.is_done:
                break

        extracted_rows = last_action_result.content['rows']
        if len(extracted_rows) > 0:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_path = os.path.abspath(f"{self.ctx.save_dir}/extracted_{timestamp}.csv")

            fieldnames = list(extracted_rows[0].keys()) if extracted_rows else []
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(extracted_rows)

            logger.info(f'Extracted {len(extracted_rows)} rows and saved to {csv_path}')
            logger.info("\n" + tabulate(extracted_rows, headers='keys', tablefmt='simple'))

            return ActionResult(action_result_msg=f'Successfully extracted and persisted {len(extracted_rows)} rows to {csv_path}', 
                                success=True, 
                                is_done=True,
                                content={'rows': extracted_rows, 'csv_path': csv_path})
        else:
            return ActionResult(action_result_msg='Extraction failed: No content was found on the page that could be extracted.', 
                                success=False, 
                                is_done=True)

    async def step(self, step_number: int) -> ActionResult:
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps, agent_name="Content Extract Agent")

        messages = self.message_manager.get_messages()
        browser_state = await my_utils.get_current_browser_state_message(current_step=step_number, browser_context=self.ctx.browser_context)
        messages.extend(browser_state)
        
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
                                              save_dir=f"{self.ctx.save_dir}/{self.ctx.agent_id:02d}_content_extract_agent")

        logger.info(f'Step {step_number} - sending messages to LLM')
        response: Response = self.ctx.openai_client.responses.create(
            model="gpt-4.1",
            input=messages,
            text=self.output_schema,
            tools=self.my_agent_tools.tools_schema,
            tool_choice='auto',
            parallel_tool_calls=False,
            store=False,
            temperature=0.0,
        )
        await self.ctx.browser_context.remove_highlights()
    
        if response.output_text:
            action_result = ActionResult(action_result_msg=f'Extraction completed.', 
                                         success=True, 
                                         is_done=True,
                                         content={'rows': json.loads(response.output_text)['rows']})
        else:
            action_result = await self.my_agent_tools.handle_tool_calls(current_step=step_number, 
                                                                        response=response,                 
                                                                        message_manager=self.message_manager)

        return action_result
    