import logging
from pathlib import Path

import my_utils
from my_agent_tools import ActionResult, MyAgentTools, BRAIN_TOOLS

logger = logging.getLogger(__name__)


class MyBrainAgent():
    def __init__(self, ctx: my_utils.MyAgentContext):
        self.max_steps = 1000
        self.ctx = ctx      
        self.my_agent_tools = MyAgentTools(ctx=self.ctx, tools=BRAIN_TOOLS)

        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_prompt())
        self.message_manager.add_user_message(content=self.get_user_prompt(),
                                              ephemeral=False)
    
    def get_user_prompt(self) -> str:
        return Path("my_brain_user_01.md").read_text()
            
    def get_system_prompt(self) -> str:
        return Path("my_brain_system_02.md").read_text()


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
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps, agent_name="Brain Agent")
                
        self.message_manager.add_ai_message(content=f"Current step: {step_number}", ephemeral=False)
        messages = self.message_manager.get_messages()
        my_utils.MessageManager.persist_state(messages=messages, step_number=step_number, save_dir=f"{self.ctx.save_dir}/brain_agent")

        logger.info(f"Step {step_number}, Sending messages to the model...")
        response = self.ctx.openai_client.responses.create(
            model="gpt-4.1",
            # reasoning={"effort": "medium", "summary": "detailed"},
            input=messages,
            tools=self.my_agent_tools.tools_schema,
            tool_choice="auto",
            parallel_tool_calls=False,
            store=False,
            temperature=0.0     # Not supported for o3 and o4-mini
        )

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
