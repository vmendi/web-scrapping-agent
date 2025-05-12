import csv
import datetime
import json
import logging
import os

from openai.types.responses import Response

from my_agent_tools import CEA_TOOLS, EXTRACTOR_TOOLS, MyAgentTools, ActionResult
import markdownify
import my_utils

logger = logging.getLogger(__name__)


class MyExtractorAgent:
    def __init__(self, ctx: my_utils.MyAgentContext, extraction_goal: str, row_schema: str):
        self.max_steps = 20
        self.ctx = ctx
        self.extraction_goal = extraction_goal
        self.message_manager = my_utils.MessageManager(system_message_content=self._read_system_prompt())

        self.message_manager.add_user_message(
            content=("You are tasked with extracting structured data from a webpage.\n"
                     f"Extraction goal: {self.extraction_goal}\n\n"
                     "The caller provided the JSON schema of a *single row* that must be adhered to:\n"
                     f"```json\n{json.dumps(row_schema, indent=2)}\n```\n"
                     "Produce a JSON array where each element respects that schema."),
            ephemeral=False
        )

        self.my_agent_tools = MyAgentTools(ctx=self.ctx, tools=EXTRACTOR_TOOLS)
        self.output_schema = my_utils.convert_simplified_schema_to_rows_in_openai_output_schema(row_schema)
        
    def _read_system_prompt(self) -> str:
        with open( "my_extractor_system_00.md", "r", encoding="utf-8") as fh:
            return fh.read()
        
    async def run(self) -> ActionResult:
        logger.info(f'Starting content-extraction task at {self.ctx.run_id}')

        action_result: ActionResult | None = None
        for step_number in range(self.max_steps):
            action_result = await self.step(step_number=step_number)

            if action_result.action_name == "output_text":
                break

        extracted_rows = action_result.content['rows'] if action_result.content else []
        if len(extracted_rows) > 0:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_path = os.path.abspath(f"{self.ctx.save_dir}/extracted_{timestamp}.csv")

            fieldnames = list(extracted_rows[0].keys()) if extracted_rows else []
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(extracted_rows)

            logger.info(f'Extracted {len(extracted_rows)} rows and saved to {csv_path}')
            
            return ActionResult(
                action_name="done",
                action_result_msg=f'Successfully extracted and persisted {len(extracted_rows)} rows to {csv_path}', 
                success=True,
                content={'rows': extracted_rows, 'csv_path': csv_path})
        else:
            return ActionResult(
                action_name="done",
                action_result_msg='Extraction failed: No content was found on the page that could be extracted.', 
                success=False)

    async def step(self, step_number: int) -> ActionResult:
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps, agent_name="Content Extract Agent")

        messages = self.message_manager.get_messages()
        messages.extend(my_utils.get_screenshot_message(browser_context=self.ctx.browser_context))
        
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
            })

        my_utils.MessageManager.persist_state(messages=messages, 
                                              step_number=step_number, 
                                              save_dir=f"{self.ctx.save_dir}/{self.ctx.agent_id:02d}_extractor_agent")

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
            action_result = ActionResult(action_name="output_text",
                                         action_result_msg=f'Extraction completed.', 
                                         success=True,
                                         content={'rows': json.loads(response.output_text)['rows']})
        else:
            action_result = await self.my_agent_tools.handle_tool_call(current_step=step_number, 
                                                                        response=response,                 
                                                                        message_manager=self.message_manager)

        return action_result