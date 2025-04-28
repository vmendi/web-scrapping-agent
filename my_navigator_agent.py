import datetime
import json
import logging
from pathlib import Path
from browser_use.browser.views import BrowserState
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
        self.agent_id = ctx.generate_next_child_agent_id()
        self.my_agent_tools = MyAgentTools(ctx=self.ctx, tools=NAVIGATOR_TOOLS)        
        self.output_schema = my_utils.convert_pydantic_model_to_openai_output_schema(NavigatorAgentOutputModel)
        
        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_message())
        self.message_manager.add_user_message(content=self.build_user_prompt(navigation_goal=navigation_goal))
        

    @staticmethod
    def get_system_message() -> str:
        return Path("my_navigator_system_prompt_00.md").read_text()
        
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
        
        browser_state = await self.ctx.browser_context.get_state()
        messages = self.message_manager.get_messages()
        
        # Add current state as the last message in the list before calling the model. We don't store it in the message manager 
        # on purpose: It's just transitory state. If the model wants to memorize anything, it will write it to its memory.
        messages.extend(self.get_current_state_message(current_step=step_number, browser_state=browser_state))
        
        my_utils.MessageManager.persist_state(messages=messages, 
                                              screenshot_base64=browser_state.screenshot, 
                                              step_number=step_number,
                                              save_dir=f"{self.ctx.save_dir}/navigator_agent_{self.agent_id:02d}")
        
        await self.ctx.browser_context.remove_highlights()
        
        logger.info(f"Step {step_number}, Sending messages to the model...")
        response = self.ctx.openai_client.responses.create(
            # model="gpt-4.1-nano",
            # model="gpt-4.1-mini",
            # model="gpt-4.1",
            model="o3",
            # model="o4-mini",
            reasoning={"effort": "high"},
            input=messages,
            text=self.output_schema,
            tools=self.my_agent_tools.tools_schema,
            tool_choice="auto",         # auto, required, none, or just one particular tool. If required, we dont get output text.
            parallel_tool_calls=False,
            store=False
        )
        
        # ACT!
        if response.output_text:
            navigator_agent_output = json.loads(response.output_text)
            self.message_manager.add_ai_message(content=json.dumps(navigator_agent_output, indent=2))
            logger.info(f"Step {step_number}, Response Message:\n{json.dumps(navigator_agent_output, indent=2)}")
        else:
            logger.info(f"Step {step_number}, Empty Response Message.")

        action_result = ActionResult(action_result_msg="No action executed. The model did not return a function tool call.", 
                                     success=True, 
                                     is_done=False)

        # Get the function tool call from the array of output messages
        function_tool_call: ResponseFunctionToolCall = next((item for item in response.output if isinstance(item, ResponseFunctionToolCall)), None)

        if function_tool_call:
            self.message_manager.add_ai_function_tool_call_message(function_tool_call=function_tool_call)
            logger.info(f"Step {step_number}, Function Tool Call:\n{function_tool_call.to_json()}")
            
            # Execute the tool
            action_result = await self.my_agent_tools.execute_tool(function_tool_call=function_tool_call)
            logger.info(f'Step {step_number}, Function Tool Call Result: {action_result.action_result_msg}')
            
            # Add the tool result message using the correct tool_call_id
            self.message_manager.add_tool_result_message(result_message=action_result.action_result_msg,
                                                         tool_call_id=function_tool_call.call_id)
        else:
            logger.info(f"Step {step_number}, No function tool call in the response")

        return action_result


    @staticmethod
    def get_current_state_message(current_step: int, browser_state: BrowserState) -> list[dict]:
        include_attributes: list[str] = [
            'title',
            'type',
            'name',
            'role',
            'aria-label',
            'placeholder',
            'value',
            'alt',
            'aria-expanded',
        ]
        elements_text = browser_state.element_tree.clickable_elements_to_string(include_attributes=include_attributes)

        has_content_above = (browser_state.pixels_above or 0) > 0
        has_content_below = (browser_state.pixels_below or 0) > 0

        if elements_text != '':
            if has_content_above:
                elements_text = f'... {browser_state.pixels_above} pixels above - scroll to see more ...\n{elements_text}'
            else:
                elements_text = f'[Start of page]\n{elements_text}'

            if has_content_below:
                elements_text = f'{elements_text}\n... {browser_state.pixels_below} pixels below - scroll to see more ...'
            else:
                elements_text = f'{elements_text}\n[End of page]'
        else:
            elements_text = '- Empty page -'

        return [
            {
                "role": "user",
                "content": f"[Current state starts here]\n"
                           f"Current step: {current_step}\n"
                           f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                           f"The following is one-time information - if you need to remember it write it to memory:\n"
                           f"Current url: {browser_state.url}\n"
                           f"Available tabs:\n{browser_state.tabs}\n"
                           f"Interactive elements from top layer of the current page inside the viewport:\n{elements_text}\n"
                           f"[Current state ends here]"
            },
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'input_text',
                        'text': 'Here is a screenshot of the current state of the browser:'
                    },
                    {
                        'type': 'input_image',
                        'image_url': f"data:image/png;base64,{browser_state.screenshot}",
                        "detail": "high"
                    }
                ]
            }
        ]