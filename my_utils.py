import datetime
import json
import logging
import os
from typing import ClassVar, Type
from pydantic import BaseModel
from agents import AgentOutputSchema
from openai.types.responses import ResponseFunctionToolCall
import base64
import copy
from dataclasses import dataclass
import json
import logging
from typing import Type
from pydantic import BaseModel
from browser_use.browser.views import BrowserState

from openai import OpenAI
from openai.types.responses import ResponseFunctionToolCall
from browser_use.browser.context import BrowserContext


logger = logging.getLogger(__name__)


@dataclass
class MyAgentContext:
    browser_context: BrowserContext
    openai_client: OpenAI
    save_dir: str
    run_id: str

    agent_id: int = -1
    memory: dict = None

    def new_agent_context(self):
        return MyAgentContext(browser_context=self.browser_context,
                              openai_client=self.openai_client,
                              save_dir=self.save_dir,
                              run_id=self.run_id,
                              agent_id=MyAgentContext.generate_next_agent_id(),
                              memory={})

    @staticmethod
    def generate_next_agent_id() -> int:
        if not hasattr(MyAgentContext.generate_next_agent_id, "_next_agent_id"):
            MyAgentContext.generate_next_agent_id._next_agent_id = 0
        next_id = MyAgentContext.generate_next_agent_id._next_agent_id
        MyAgentContext.generate_next_agent_id._next_agent_id += 1
        return next_id
    

def convert_pydantic_model_to_openai_output_schema(model: Type[BaseModel]) -> dict:    
    agent_output_schema = AgentOutputSchema(model, strict_json_schema=True)
    schema = agent_output_schema.json_schema()

    return {
        "format": {
            "type": "json_schema",
            "name": model.__name__,
            "schema": schema,
            "strict": True
        }
    }


def convert_simplified_schema_to_rows_in_openai_output_schema(row_schema: str) -> dict:
    """Convert a simplified schema (where keys are field names and values are types) into a proper OpenAI output schema.
    
    Args:
        row_schema: str - A JSON string containing a simplified schema where keys are field names and values are types.
            Example: {"some_name": "string", "the_age": "integer"}
            
    Returns:
        dict - A properly formatted OpenAI output schema
    """
    row_schema_dict = json.loads(row_schema)

    # Convert the simplified schema to proper JSON schema format
    properties = {}
    for key, type_str in row_schema_dict.items():
        properties[key] = {"type": type_str}
            
    return {
        "format": {
            "type": "json_schema",
            "name": "ExtractedRows",
            "schema": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": properties,
                            "required": list(properties.keys()),
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["rows"],
                "additionalProperties": False
            },
            "strict": True
        }
    }


class MessageManager:
    def __init__(self, system_message_content: str):
        self._messages: list[dict] = [{
            "role": "system",
            "content": system_message_content
        }]
        
    def add_user_message(self, content: str):
        """Adds a user (human) message."""
        self._messages.append({
            "role": "user",
            "content": content
        })

    def add_ai_message(self, content: str):
        self._messages.append({
            "role": "assistant",
            "content": content,
        })

    def add_ai_function_tool_call_message(self, function_tool_call: ResponseFunctionToolCall):
        """Adds an a message that contains a function tool call that the model wants to execute."""
        self._messages.append({
            "type": "function_call",
            "call_id": function_tool_call.call_id,
            "name": function_tool_call.name,
            "arguments": function_tool_call.arguments
        })
        
    def add_tool_result_message(self, result_message: str, tool_call_id: str):
        """Adds the result message of a tool call."""
        self._messages.append({
            "type": "function_call_output",
            "call_id": tool_call_id,
            "output": result_message
        })

    def get_messages(self) -> list[dict]:
        return copy.deepcopy(self._messages)
    
    @staticmethod
    def persist_state(messages: list[dict], step_number: int, save_dir: str):
        os.makedirs(save_dir, exist_ok=True)
        
        # 1. Persist human-readable log + raw JSON (with screenshots redacted)
        state_file_name = f"{save_dir}/step_{step_number:02d}_messages"
        with open(f"{state_file_name}.txt", "w") as f:
            formatted_messages = MessageManager.get_pretty_formatted_messages(messages=messages, step_number=step_number)
            f.write(formatted_messages)

        with open(f"{state_file_name}.json", "w") as f:
            json.dump(MessageManager.get_json_messages(messages=messages), f, indent=2)

        # 2. Extract every screenshot embedded in the messages and persist each one
        screenshots: list[str] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "input_image":
                        image_url: str = item.get("image_url", "")
                        prefix = "data:image/png;base64,"
                        if image_url.startswith(prefix):
                            screenshots.append(image_url[len(prefix):])

        for idx, screenshot_base64 in enumerate(screenshots):
            screenshot_file_name = f"{save_dir}/step_{step_number:02d}_screenshot_{idx:02d}.png"
            with open(screenshot_file_name, "wb") as f:
                f.write(base64.b64decode(screenshot_base64))
            

    @staticmethod
    def get_json_messages(messages: list[dict]):
        messages_for_json = copy.deepcopy(messages)
        for message in messages_for_json:
            if isinstance(message.get('content'), list):
                for item in message['content']:
                    if item.get('type') == 'input_image':
                        item['image_url'] = 'REDACTED'
        return messages_for_json

    @staticmethod
    def get_pretty_formatted_messages(messages: list[dict], step_number: int):
        messages_for_log = messages.copy()

        call_id_to_index_map: dict[str, int] = {}
        for message in messages_for_log:
            if message.get('type') == 'function_call':
                call_id_to_index_map[message.get('call_id')] = len(call_id_to_index_map)
        
        # Let's build the message log in a format amenable for human reading
        formatted_messages = []
        for message in messages_for_log:
            if not isinstance(message, dict):
                formatted_messages.append(f"Unknown message type: {message}")
                continue
            
            if 'role' in message:                
                role = message.get('role', 'unknown')
                content = message.get('content', 'empty')
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            content = f"Unknown content type: {item}"
                            continue
                        if item.get('type') == 'input_text':
                            content = item.get('content', 'unknown')
                        elif item.get('type') == 'input_image':
                            content = f"Image URL: Redacted"
                formatted_messages.append(f"\n---------------------- role:{role} ----------------------\n{content}")
            elif 'type' in message:
                type = message.get('type', 'unknown')
                if type == 'function_call':
                    content = (f"call_id: {message.get('call_id', 'unknown')}\n"
                               f"call_id_index: {call_id_to_index_map.get(message.get('call_id'), 'unknown')}\n"
                               f"name: {message.get('name', 'unknown')}\n"
                               f"arguments: {message.get('arguments', 'unknown')}")
                elif type == 'function_call_output':
                    content = (f"call_id: {message.get('call_id', 'unknown')}\n"
                               f"call_id_index: {call_id_to_index_map.get(message.get('call_id'), 'unknown')}\n"
                               f"output: {message.get('output', 'unknown')}")
                else:
                    content = f"Unknown type: {type}"
                formatted_messages.append(f"\n---------------------- type:{type} ----------------------\n{content}")
            else:
                formatted_messages.append(f"Unknown message format: {message}")
                
        formatted_messages_str = "\n\n".join(formatted_messages)
        formatted_messages_str = f"---------------------- Step {step_number} messages ----------------------\n" \
                                 f"{formatted_messages_str}\n\n" \
                                 f"---------------------- Step {step_number} end of messages ----------------------\n"
        return formatted_messages_str


def log_step_info(logger: logging.Logger, step_number: int, max_steps: int) -> None:
    step_message = f'----------------------------------- Step {step_number} of {max_steps} -----------------------------------'
    border_line = '-' * len(step_message)
    logger.info(f"\n{border_line}\n{step_message}\n{border_line}")




@staticmethod
def get_current_browser_state_message(current_step: int, browser_state: BrowserState) -> list[dict]:
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
            "content": [
                {
                    "type": "input_text",
                    "text": f"[Current state starts here]\n"
                            f"Current step: {current_step}\n"
                            f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                            f"Current url: {browser_state.url}\n"
                            f"Available tabs:\n{browser_state.tabs}\n"
                            f"Interactive elements from top layer of the current page inside the viewport:\n{elements_text}\n"
                            f"[Current state ends here]"
                },
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{browser_state.screenshot}",
                    "detail": "high"
                }
            ]
        }
    ]