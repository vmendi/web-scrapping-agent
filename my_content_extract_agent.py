import json
import logging

from openai.types.responses import Response

from my_agent_tools import CEA_TOOLS, MyAgentTools, ActionResult
import my_utils

logger = logging.getLogger(__name__)


class MyContentExtractAgent:
    def __init__(self, ctx: my_utils.MyAgentContext, extraction_goal: str, row_schema: str):
        self.max_steps = 20
        self.ctx = ctx
        self.extraction_goal = extraction_goal
        self.message_manager = my_utils.MessageManager(system_message_content=self._read_system_prompt())

        self.message_manager.add_user_message(
            content=(f"extraction_goal: {self.extraction_goal}\n\n"
                     "json_schema:\n"
                     f"```json\n{json.dumps(row_schema, indent=2)}\n```\n"),
            ephemeral=False
        )

        self.my_agent_tools = MyAgentTools(ctx=self.ctx, tools=CEA_TOOLS) 

    def _read_system_prompt(self) -> str:
        with open( "my_content_extract_system_03.md", "r", encoding="utf-8") as fh:
            return fh.read()

 
    async def run(self) -> ActionResult:
        logger.info(f'Starting planning task at {self.ctx.run_id}')
        
        for step_number in range(self.max_steps):
            action_result = await self.step(step_number=step_number)
            
            if action_result.action_name == "done":
                logger.info(f'Task completed at step {step_number} with success: {action_result.success}')
                break
        else:
            logger.error(f'Task failed after max {self.max_steps} steps')
            action_result = ActionResult(action_name="done",
                                         action_result_msg=f"Task failed after max {self.max_steps} steps",
                                         success=False)

        return action_result

    async def step(self, step_number: int) -> ActionResult:
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps, agent_name="Content Extract Agent")

        messages = self.message_manager.get_messages()
        browser_state = await my_utils.get_current_browser_state_message(current_step=step_number, browser_context=self.ctx.browser_context)
        messages.extend(browser_state)
        my_utils.MessageManager.persist_state(messages=messages, 
                                              step_number=step_number, 
                                              save_dir=f"{self.ctx.save_dir}/{self.ctx.agent_id:02d}_content_extract_agent")

        logger.info(f'Step {step_number} - sending messages to LLM')
        response: Response = self.ctx.openai_client.responses.create(
            model="gpt-4.1",
            input=messages,
            tools=self.my_agent_tools.tools_schema,
            tool_choice='auto',
            parallel_tool_calls=False,
            store=False,
            temperature=0.0,
        )
        await self.ctx.browser_context.remove_highlights()

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        total_tokens = response.usage.total_tokens
        logger.info(f"Step {step_number}, Input Tokens: {input_tokens}, Output Tokens: {output_tokens}, Total Tokens: {total_tokens}")
    
        if response.output_text:
            logger.info(f"Step {step_number}, Response Message:\n{response.output_text}")
            self.message_manager.add_ai_message(content=response.output_text, ephemeral=False)
            action_result = ActionResult(action_name="output_text",
                                         action_result_msg=f"{response.output_text}",
                                         success=True)
        else:
            action_result = await self.my_agent_tools.handle_tool_call(current_step=step_number, 
                                                                       response=response,                 
                                                                       message_manager=self.message_manager)

        return action_result
    