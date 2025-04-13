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
from openai.types.responses import ResponseFunctionToolCall
from pydantic import BaseModel, ConfigDict, Field, create_model
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, ToolMessage, AIMessage

from my_agent_tools import MyAgentTools

logger = logging.getLogger(__name__)


def get_openai_schema(model_class: Type[BaseModel]) -> dict:
    """Generate the OpenAI format schema from a Pydantic model"""
    # Get the base JSON schema from Pydantic
    base_schema = model_class.model_json_schema()
    
    # Clean up the schema to match OpenAI's format
    # Remove any Pydantic-specific metadata
    if "title" in base_schema:
        del base_schema["title"]
    if "description" in base_schema:
        del base_schema["description"]    

    base_schema["additionalProperties"] = False
    
    # Transform into OpenAI format
    openai_schema = {
        "format": {
            "type": "json_schema",
            "name": model_class.__name__.lower(),
            "schema": base_schema,
            "strict": True
        }
    }
    return openai_schema


class AgentOutputModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    evaluation_previous_goal: str
    memory: str
    next_goal: str


class MessageManager:
    def __init__(self, system_message: SystemMessage):
        self._messages = [system_message]
        self._tool_id = 0

    def add_message(self, message: BaseMessage):
        self._messages.append(message)

    def add_human_message(self, message: HumanMessage):
        self._messages.append(message)

    def add_ai_message(self, message: AIMessage):
        self._messages.append(message)

    def add_agent_model_output(self, agent_output_model: AgentOutputModel):
        self._messages.append(AIMessage(
            content='',
            tool_calls=[
                {
                    'name': 'AgentOutputModel',
                    'args': agent_output_model.model_dump(mode='json', exclude_unset=True),
                    'id': str(self._tool_id),
                    'type': 'tool_call',
                }
            ]
        ))
            
    def add_action_result(self, action_result: ActionResult):
        self._messages.append(ToolMessage(
            content=f'Action result: {action_result.extracted_content}',
            name='ActionResult',
            tool_call_id=str(self._tool_id),
        ))

    def get_all_messages(self) -> list[BaseMessage]:
        return self._messages

    def get_all_messages_openai_format(self) -> list[dict]:
        """Convert internal messages to OpenAI format"""
        openai_messages = []
        for message in self._messages:
            if isinstance(message, SystemMessage):
                openai_messages.append({
                    "role": "system",
                    "content": message.content
                })
            elif isinstance(message, HumanMessage):
                openai_messages.append({
                    "role": "user",
                    "content": message.content
                })
            elif isinstance(message, AIMessage):
                msg = {
                    "role": "assistant",
                    "content": message.content
                }
                if hasattr(message, 'tool_calls') and message.tool_calls:
                    msg["tool_calls"] = message.tool_calls
                openai_messages.append(msg)
            elif isinstance(message, ToolMessage):
                openai_messages.append({
                    "role": "tool",
                    "content": message.content,
                    "tool_call_id": message.tool_call_id
                })
        return openai_messages


class MyAgent():
    def __init__(self, 
                 web_scrapping_task: str,
                 browser: Browser, 
                 browser_context: BrowserContext,
                 ):
        self.web_scrapping_task = web_scrapping_task
        self.browser = browser
        self.browser_context = browser_context

        self.openai_client = OpenAI()

        self.my_agent_tools = MyAgentTools(browser_context=self.browser_context, 
                                           openai_client=self.openai_client)
        
        self.output_schema = get_openai_schema(AgentOutputModel)
        
        self.system_message = self.get_system_message()
        self.message_manager = MessageManager(system_message=self.system_message)

        first_human_message = HumanMessage(content=f'Your Web Scrapping task is:\n"""{self.web_scrapping_task}"""')
        self.message_manager.add_human_message(message=first_human_message)
        

    @staticmethod
    def get_system_message() -> SystemMessage:
        with open("my_agent_system_prompt_00.md", "r") as f:
            system_prompt = f.read()
            return SystemMessage(content=system_prompt)

    async def run(self, max_steps: int = 1000):
        logger.info(f'Starting task: {self.web_scrapping_task}')
        
        for step_number in range(max_steps):
            await self.step(step_number=step_number, max_steps=max_steps)
        
        logger.info(f'Task completed')
    
    async def step(self, step_number: int, max_steps: int):
        logger.info(f'Step {step_number} of {max_steps}')
        
        browser_state = await self.browser_context.get_state()
        self.save_screenshot(browser_state.screenshot, step_number)

        messages = self.message_manager.get_all_messages_openai_format()

        # Add current state as the last message in the list before calling the model. We don't store it in the message manager 
        # on purpose. It's just state. If the model wants to memorize anything, it will write it to its memory.
        messages.extend(self.get_current_state_message(current_step=step_number, browser_state=browser_state))
        
        response = self.openai_client.responses.create(
            model="gpt-4o",
            input=messages,
            text=self.output_schema,
            tools=self.my_agent_tools.tools_schema,
            tool_choice="required",         # Auto, none, or just one particular tool
            parallel_tool_calls=False,
            store=False
        )

        # ACT!
        if len(response.output) != 1:
            raise ValueError(f"Expected 1 tool call, got {len(response.output)}")
        
        if not isinstance(response.output[0], ResponseFunctionToolCall):
            raise ValueError(f"The response must be a function call:\n{json.dumps(response.output[0], indent=2)}")

        function_tool_call: ResponseFunctionToolCall = response.output[0]
        logger.info(f"Step {step_number}, Action:\n{function_tool_call.to_json()}")      
        
        action_result = await self.my_agent_tools.execute_tool(function_tool_call=function_tool_call)

        logger.info(f'Action result: {action_result.extracted_content}')
        self.message_manager.add_action_result(action_result=action_result)


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
                           f"Current step: {current_step + 1}\n"
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


    @staticmethod
    def save_screenshot(screenshot_base64: str, step_number: int) -> str:
        """
        Save the base64 encoded screenshot to disk
        
        Args:
            screenshot_base64: Base64 encoded PNG image
            step_number: Current step number for the filename
            
        Returns:
            The path to the saved screenshot
        """
        # Create screenshots directory if it doesn't exist
        screenshots_dir = "screenshots"
        os.makedirs(screenshots_dir, exist_ok=True)
        
        # Generate filename with timestamp and step number
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{screenshots_dir}/screenshot_{timestamp}_step{step_number}.png"
        
        # Decode base64 and write to file
        with open(filename, "wb") as f:
            f.write(base64.b64decode(screenshot_base64))
            
        logger.info(f"Screenshot saved to {filename}")
        return filename
        
        