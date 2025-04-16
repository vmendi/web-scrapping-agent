import base64
import datetime
import json
import os
import logging
from typing import Generic, Optional, Type, TypeVar
from agents import AgentOutputSchema
from browser_use import ActionResult, Browser, BrowserConfig, BrowserContextConfig
from browser_use.browser.context import BrowserContext
from browser_use.browser.views import BrowserState
import asyncio
from openai import OpenAI
from openai.types.responses import ResponseFunctionToolCall
from pydantic import BaseModel, ConfigDict, Field, create_model
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, ToolMessage, AIMessage

import my_utils
logger = logging.getLogger(__name__)


class PlannerAgentOutputModel(BaseModel):
    class OutputField(BaseModel):
        name: str = Field(description="The snake_case name of the output field")
        type: str = Field(description="The data type of the output field (e.g., 'string', 'integer', 'boolean', 'array', 'object')")
        description: str = Field(description="A brief description of what this field represents")

    class PlanStep(BaseModel):
        step_id: int = Field(description="Sequential identifier for the step")
        agent: str = Field(description="Must always be 'WNA'")
        goal: str = Field(description="The objective for the WNA in this step")
        input_hints: list[str] = Field(description="High-level guidance/keywords for WNA")
        output_criteria: str = Field(description="Description of the successful outcome for this WNA step")

    output_schema: list[OutputField] = Field(
        description="A list defining the fields for the final output object. Each field should have a name (snake_case), type, and description."
    )
    plan: list[PlanStep] = Field(
        description="A sequential list of steps only for the WNA"
    )


class MyPlannerAgent():
    def __init__(self, 
                 browser: Browser, 
                 browser_context: BrowserContext,
                 ):
        self.browser = browser
        self.browser_context = browser_context
        self.system_prompt_file = "my_planner_system_prompt.md"
        self.user_prompt_file = "my_planner_user_prompt.md"

        self.openai_client = OpenAI()       
        self.output_schema = my_utils.convert_pydantic_model_to_openai_output_schema(PlannerAgentOutputModel)
        
        self.system_message = self.get_system_prompt()
        self.message_manager = my_utils.MessageManager(system_message=self.system_message)
        self.message_manager.add_human_message(message=self.get_user_prompt())
    
    def get_user_prompt(self) -> HumanMessage:
        with open(self.user_prompt_file, "r", encoding="utf-8") as f:
            user_prompt = f.read()
            return HumanMessage(content=user_prompt)

    def get_system_prompt(self) -> SystemMessage:
        with open(self.system_prompt_file, "r", encoding="utf-8") as f:
            system_prompt = f.read()
            return SystemMessage(content=system_prompt)

    async def run(self, max_steps: int = 1000):
        logger.info(f'Starting planning task...')
        
        for step_number in range(max_steps):
            done = await self.step(step_number=step_number, max_steps=max_steps)
            if done:
                break
        
        logger.info(f'Task completed')
    
    async def step(self, step_number: int, max_steps: int):
        logger.info(f'Step {step_number} of {max_steps}')
        
        messages = self.message_manager.get_all_messages_openai_format()
        logger.info(f'Messages: {json.dumps(messages, indent=2)}')
        
        response = self.openai_client.responses.create(
            # model="gpt-4.1-nano",
            # model="gpt-4.1-mini",
            model="gpt-4.1",
            input=messages,
            text=self.output_schema,
            tool_choice="auto",         # auto, none, or just one particular tool
            parallel_tool_calls=False,
            store=False
        )
                
        response_json = json.loads(response.output_text)
        logger.info(f'Response: {json.dumps(response_json, indent=2)}')

        return True