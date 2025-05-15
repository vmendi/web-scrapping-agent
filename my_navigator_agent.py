import logging
from pathlib import Path
from my_agent_tools import ActionResult, MyAgentTools, NAVIGATOR_TOOLS
import my_utils

logger = logging.getLogger(__name__)


class MyNavigatorAgent():
    def __init__(self, ctx: my_utils.MyAgentContext, navigation_goal: str):
        self.max_steps = 100
        self.ctx = ctx
        self.my_agent_tools = MyAgentTools(ctx=self.ctx, tools=NAVIGATOR_TOOLS) 
               
        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_message())
        self.message_manager.add_user_message(content=self.build_user_prompt(navigation_goal=navigation_goal),
                                              ephemeral=False)
        

    @staticmethod
    def get_system_message() -> str:
        return Path("my_navigator_system_02.md").read_text()
        
    @staticmethod
    def build_user_prompt(navigation_goal: str) -> str:
        return Path("my_navigator_user_00.md").read_text().format(navigation_goal=navigation_goal)

    async def run(self) -> ActionResult:
        logger.info(f'Starting navigator agent task at {self.ctx.run_id}')
        
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
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps, agent_name="Navigator Agent")
        
        messages = self.message_manager.get_messages()
        browser_state_message = await my_utils.get_current_browser_state_message(
            current_step=step_number, 
            browser_context=self.ctx.browser_context,
            include_screenshot=True
        )
        messages.extend(browser_state_message)
        my_utils.MessageManager.persist_state(messages=messages, 
                                              step_number=step_number,
                                              save_dir=f"{self.ctx.save_dir}/{self.ctx.agent_id:02d}_navigator_agent")
                
        logger.info(f"Step {step_number}, Sending messages to the model...")
        response = self.ctx.openai_client.responses.create(
            # model="gpt-4.1-nano",
            # model="gpt-4.1-mini",
            model="gpt-4.1",
            # model="o3",
            # model="o4-mini",
            # reasoning={"effort": "medium"},
            input=messages,
            tools=self.my_agent_tools.tools_schema,
            tool_choice="auto",
            parallel_tool_calls=False,
            store=False,
            temperature=0.0     # Not supported for o3 and o4-mini
        )
        my_utils.log_openai_response_info(logger=logger, response=response, step_number=step_number)

        await self.ctx.browser_context.remove_highlights()

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