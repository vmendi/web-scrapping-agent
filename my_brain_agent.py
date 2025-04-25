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


class MyBrainAgent():
    def __init__(self, ctx: my_utils.MyAgentContext):
        self.ctx = ctx
        self.system_prompt_file = "my_brain_system_prompt_00.md"
        self.user_prompt_file = "my_brain_user_prompt_01.md"

        self.output_schema = my_utils.convert_pydantic_model_to_openai_output_schema(PlannerAgentOutputModel)
        
        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_prompt())
        self.message_manager.add_user_message(content=self.get_user_prompt())
    
    def get_user_prompt(self) -> str:
        with open(self.user_prompt_file, "r", encoding="utf-8") as f:
            return f.read()
            
    def get_system_prompt(self) -> str:
        with open(self.system_prompt_file, "r", encoding="utf-8") as f:
            return f.read()


    async def run(self, max_steps: int = 1000):
        logger.info(f'Starting planning task at {self.ctx.run_id}')
        
        for step_number in range(max_steps):
            is_done, is_success = await self.step(step_number=step_number, max_steps=max_steps)
            if is_done:
                logger.info(f'Task completed at step {step_number} with success: {is_success}')
                break
        else:
            logger.info(f'Task failed after max {max_steps} steps')
    

    async def step(self, step_number: int, max_steps: int):
        my_utils.log_step_info(logger, step_number, max_steps)
        
        messages = self.message_manager.get_messages()        
        messages.append({
            "role": "user",
            "content": f"Current step: {step_number}\n"
                       f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        })
        
        logger.info(f"Step {step_number}, Sending messages to the model...")
        response = self.ctx.openai_client.responses.create(
            # model="o4-mini",
            model="o3",
            # reasoning={"effort": "medium"},
            reasoning={"effort": "high"},
            input=messages,
            text=self.output_schema,
            tool_choice="auto",
            parallel_tool_calls=False,
            store=False
        )
        
        is_done = True
        is_success = False

        if response.output_text:
            planner_agent_output = json.loads(response.output_text)
            self.message_manager.add_ai_message(content=json.dumps(planner_agent_output, indent=2))
            logger.info(f"Step {step_number}, Response Message:\n{json.dumps(planner_agent_output, indent=2)}")

            if planner_agent_output.get('plan') and planner_agent_output.get('output_schema'):
                with open(f"my_navigator_agent_user_prompt_00.json", 'w') as f:
                    json.dump(planner_agent_output, f, indent=2)
                
                is_success = True
        else:
            logger.info(f"Step {step_number}, Empty Response Message.")

        return is_done, is_success
