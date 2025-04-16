import logging
from typing import Type
from browser_use import ActionResult
from pydantic import BaseModel
from agents import AgentOutputSchema
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, ToolMessage, AIMessage


logger = logging.getLogger(__name__)


def convert_pydantic_model_to_openai_output_schema(model: Type[BaseModel]) -> dict:    
    agent_output_schema = AgentOutputSchema(model, strict_json_schema=True)
    schema = agent_output_schema.json_schema()

    # Construct the dictionary in the format OpenAI expects
    return {
        "format": {
            "type": "json_schema",
            "name": model.__name__,
            "schema": schema,
            "strict": True
        }
    }


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

    def add_agent_model_output(self, agent_output_model: BaseModel):
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
