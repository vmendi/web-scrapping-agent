import json
import logging
from pathlib import Path
from openai.types.responses import ResponseFunctionToolCall
from pydantic import BaseModel, ConfigDict
from my_agent_tools import ActionResult, MyAgentTools, NAVIGATOR_TOOLS
import my_utils

logger = logging.getLogger(__name__)


class NavigatorAgentOutputModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
     
    evaluation_previous_goal: str
    memory: str
    next_goal: str


class MyNavigatorAgent():
    def __init__(self, ctx: my_utils.MyAgentContext, navigation_goal: str):
        self.max_steps = 100
        self.ctx = ctx
        self.my_agent_tools = MyAgentTools(ctx=self.ctx, tools=NAVIGATOR_TOOLS)        
        self.output_schema = my_utils.convert_pydantic_model_to_openai_output_schema(NavigatorAgentOutputModel)
        
        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_message())
        self.message_manager.add_user_message(content=self.build_user_prompt(navigation_goal=navigation_goal))
        

    @staticmethod
    def get_system_message() -> str:
        return Path("my_navigator_system_prompt_01.md").read_text()
        
    @staticmethod
    def build_user_prompt(navigation_goal: str) -> str:
        return Path("my_navigator_user_prompt_00.md").read_text().format(navigation_goal=navigation_goal)

    async def run(self) -> ActionResult:
        logger.info(f'Starting navigator agent task at {self.ctx.run_id}')
        
        for step_number in range(self.max_steps):
            action_result = await self.step(step_number=step_number)
            if action_result.is_done:
                logger.info(f'Task completed at step {step_number} with success: {action_result.success}')
                break
        else:
            logger.info(f'Task failed after max {self.max_steps} steps')

        return action_result
    
    async def step(self, step_number: int) -> ActionResult:
        my_utils.log_step_info(logger=logger, step_number=step_number, max_steps=self.max_steps)
        
        messages = self.message_manager.get_messages()
        
        browser_state_message = await my_utils.get_current_browser_state_message(current_step=step_number, 
                                                                                 browser_context=self.ctx.browser_context)
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
            # text=self.output_schema,
            tools=self.my_agent_tools.tools_schema,
            tool_choice="auto",
            parallel_tool_calls=False,
            store=False,
            temperature=0.0     # Not supported for o3 and o4-mini
        )
        await self.ctx.browser_context.remove_highlights()

        if response.output_text:
            # navigator_agent_output = json.loads(response.output_text)
            # logger.info(f"Step {step_number}, Response Message:\n{json.dumps(navigator_agent_output, indent=2)}")
            # self.message_manager.add_ai_message(content=json.dumps(navigator_agent_output, indent=2))            
            logger.info(f"Step {step_number}, Response Message:\n{response.output_text}")
            self.message_manager.add_ai_message(content=response.output_text)
            action_result = ActionResult(action_result_msg="No action executed. The model output is text.", 
                                         success=True, 
                                         is_done=False)
        else:
            function_tool_call: ResponseFunctionToolCall = next((item for item in response.output if isinstance(item, ResponseFunctionToolCall)), None)
            if not function_tool_call:
                raise Exception(f"Step {step_number}, No function tool call or response output text")
            
            self.message_manager.add_ai_function_tool_call_message(function_tool_call=function_tool_call)
            logger.info(f"Step {step_number}, Function Tool Call:\n{function_tool_call.to_json()}")
            
            action_result = await self.my_agent_tools.execute_tool(function_tool_call=function_tool_call)
            logger.info(f'Step {step_number}, Function Tool Call Result: {action_result.action_result_msg}')
            
            self.message_manager.add_tool_result_message(result_message=action_result.action_result_msg,
                                                         tool_call_id=function_tool_call.call_id)
    
        return action_result