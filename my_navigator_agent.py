import base64
import datetime
import json
import os
import logging
from typing import Generic, Optional, Type, TypeVar
from browser_use import ActionResult, Browser, BrowserConfig, BrowserContextConfig
from browser_use.browser.context import BrowserContext
from browser_use.browser.views import BrowserState
import asyncio
from openai import OpenAI
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage
from pydantic import BaseModel, ConfigDict, Field, create_model
import pprint

from my_navigator_agent_tools import MyAgentTools
import my_utils


logger = logging.getLogger(__name__)


class NavigatorAgentOutputModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
     
    evaluation_previous_goal: str
    memory: str
    next_goal: str


class MyNavigatorAgent():
    def __init__(self, 
                 browser: Browser, 
                 browser_context: BrowserContext,
                 ):
        self.browser = browser
        self.browser_context = browser_context

        self.openai_client = OpenAI()

        self.my_agent_tools = MyAgentTools(browser_context=self.browser_context, 
                                           openai_client=self.openai_client)
        
        self.output_schema = my_utils.convert_pydantic_model_to_openai_output_schema(NavigatorAgentOutputModel)
        
        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_message())
        self.message_manager.add_user_message(content=self.get_web_scrapping_task())

    @staticmethod
    def get_system_message() -> str:
        with open("my_navigator_agent_system_prompt_00.md", "r") as f:
            system_prompt = f.read()
            return system_prompt
        
    @staticmethod
    def get_web_scrapping_task() -> str:
        return """Get a list of all of the Harvard University's schools.
The output columns are:
    School Name,
    School Website URL.
"""
#         return """
# "plan": [
#     {
#       "step_id": 1,
#       "goal": "Perform web searches to find the single most authoritative webpage listing Harvard University's primary academic schools.",
#       "input_hints": [
#         "search: Harvard University primary academic schools",
#         "search: Harvard University list of schools",
#         "search: Harvard University degree programs",
#         "look for: navigation links like 'Academics', 'Schools', 'Admissions' on potential university homepages"
#       ],
#       "output_criteria": "Output the single URL identified as the most likely official directory or listing page for Harvard University's primary schools."
#     },
#     {
#       "step_id": 2,
#       "goal": "Navigate to the URL identified in Step 1 and locate the main section or list containing representations of the primary academic schools.",
#       "input_hints": [
#         "look for: main content sections with headings like 'Schools', 'Our Schools', 'Academic Divisions', 'Degree Programs'",
#         "identify: patterns of repeating elements where each seems to represent a school (e.g., name with a link)",
#         "apply constraint: focus on lists clearly representing major degree-granting academic divisions, differentiating from lists of departments, centers, or institutes"
#       ],
#       "output_criteria": "Report the final URL and provide a clear description or context for the primary page region (e.g., container element, section) holding the school list"
#     },
#     {
#       "step_id": 3,
#       "goal": "Persist the list of schools by using extract_content tool",
#       "input_hints": [],
#       "output_criteria": ""
#     }
#   ]
# """

    async def run(self, max_steps: int = 1000):
        global_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        logger.info(f'Starting navigator agent task at {global_timestamp}')
        
        for step_number in range(max_steps):
            is_done, is_success = await self.step(step_number=step_number, 
                                                  max_steps=max_steps, 
                                                  timestamp=global_timestamp)
            if is_done:
                logger.info(f'Task completed at step {step_number} with success: {is_success}')
                break
        else:
            logger.info(f'Task failed after max {max_steps} steps')


    async def step(self, step_number: int, max_steps: int, timestamp: str):
        step_message = f'----------------------------------- Step {step_number} of {max_steps} -----------------------------------'
        border_line = '-' * len(step_message)
        logger.info(f"\n{border_line}\n{step_message}\n{border_line}")
        
        browser_state = await self.browser_context.get_state()
        messages = self.message_manager.get_messages()
        
        # Add current state as the last message in the list before calling the model. We don't store it in the message manager 
        # on purpose: It's just transitory state. If the model wants to memorize anything, it will write it to its memory.
        messages.extend(self.get_current_state_message(current_step=step_number, browser_state=browser_state))
        
        my_utils.MessageManager.persist_state(messages=messages, 
                                              screenshot_base64=browser_state.screenshot, 
                                              step_number=step_number, 
                                              timestamp=timestamp)
        
        await self.browser_context.remove_highlights()
        
        logger.info(f"Step {step_number}, Sending messages to the model...")
        response = self.openai_client.responses.create(
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
        logger.info(f"Step {step_number}, Received response from the model.")
        
        is_done = False
        is_success = True

        # ACT!
        if response.output_text:
            navigator_agent_output = json.loads(response.output_text)
            self.message_manager.add_ai_message(content=json.dumps(navigator_agent_output, indent=2))
            logger.info(f"Step {step_number}, Response Message:\n{json.dumps(navigator_agent_output, indent=2)}")
        else:
            logger.info(f"Step {step_number}, Response Message is empty: The LLM didn't return any output_text. But it may have returned a function tool call.")

        # Get the function tool call from the array of output messages
        function_tool_call: ResponseFunctionToolCall = next((item for item in response.output if isinstance(item, ResponseFunctionToolCall)), None)

        if function_tool_call:
            self.message_manager.add_ai_function_tool_call_message(function_tool_call=function_tool_call)
            logger.info(f"Step {step_number}, Action:\n{function_tool_call.to_json()}")

            # Execute the tool
            action_result = await self.my_agent_tools.execute_tool(function_tool_call=function_tool_call)
            logger.info(f'Step {step_number}, Action Result: {action_result.action_result_msg}')
            
            # Add the tool result message using the correct tool_call_id
            self.message_manager.add_tool_result_message(result_message=action_result.action_result_msg,
                                                        tool_call_id=function_tool_call.call_id)

            is_done = action_result.is_done
            is_success = action_result.success
        else:
            logger.info(f"Step {step_number}, No function tool call in the response")

        return is_done, is_success


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
                elements_text = f'... {browser_state.pixels_above} pixels above - scroll or extract content to see more ...\n{elements_text}'
            else:
                elements_text = f'[Start of page]\n{elements_text}'

            if has_content_below:
                elements_text = f'{elements_text}\n... {browser_state.pixels_below} pixels below - scroll or extract content to see more ...'
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
                           f"Interactive elements from top layer of the current page inside the viewport:\n{elements_text}"
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
            },
            {
                'role': 'user',
                'content': f'[Current state ends here]'
            }
        ]