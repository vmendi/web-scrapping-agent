import base64
import datetime
import json
import os
import logging
from pathlib import Path
from typing import Generic, Optional, Type, TypeVar
from agents import AgentOutputSchema
from browser_use import Browser, BrowserConfig, BrowserContextConfig
from browser_use.browser.context import BrowserContext
from browser_use.browser.views import BrowserState
import asyncio
from openai import OpenAI
from openai.types.responses import ResponseFunctionToolCall
from pydantic import BaseModel, ConfigDict, Field, create_model

import my_utils
from my_agent_tools import MyBrainAgentTools, ActionResult

logger = logging.getLogger(__name__)


# class PlannerAgentOutputModel(BaseModel):
#     class OutputField(BaseModel):
#         name: str = Field(description="The snake_case name of the output field")
#         type: str = Field(description="The data type of the output field (e.g., 'string', 'integer', 'boolean', 'array', 'object')")
#         description: str = Field(description="A brief description of what this field represents")

#     class PlanStep(BaseModel):
#         step_id: int = Field(description="Sequential identifier for the step")
#         goal: str = Field(description="The objective for the Brain Agent in this step. It should be a concise description of what the agent should accomplish using the tools available.")
#         success_criteria: str = Field(description="Description of a successful outcome for this step")

#     output_schema: list[OutputField] = Field(
#         description="A list defining the fields for the final output object. Each field should have a name (snake_case), type, and description."
#     )
#     plan: list[PlanStep] = Field(
#         description="A sequential list of steps that represents the current plan for the Brain Agent"
#     )


class MyBrainAgent():
    def __init__(self, ctx: my_utils.MyAgentContext):
        self.max_steps = 1000
        self.ctx = ctx
        # self.output_schema = my_utils.convert_pydantic_model_to_openai_output_schema(PlannerAgentOutputModel)
        
        self.my_agent_tools = MyBrainAgentTools(ctx=self.ctx)

        self.message_manager = my_utils.MessageManager(system_message_content=self.get_system_prompt())
        self.message_manager.add_user_message(content=self.get_user_prompt())
    
    def get_user_prompt(self) -> str:
        return Path("my_brain_user_prompt_01.md").read_text()
            
    def get_system_prompt(self) -> str:
        return Path("my_brain_system_prompt_01.md").read_text()


    async def run(self) -> ActionResult:
        logger.info(f'Starting planning task at {self.ctx.run_id}')
        
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
                
        self.message_manager.add_ai_message(content=f"Current step: {step_number}")
        messages = self.message_manager.get_messages()
        messages.append({
            "role": "assistant",
            "content": f"Current plan: {json.dumps(self.ctx.memory.get('plan', {}))}"
        })
        my_utils.MessageManager.persist_state(messages=messages, 
                                              step_number=step_number,
                                              save_dir=f"{self.ctx.save_dir}/brain_agent")

        logger.info(f"Step {step_number}, Sending messages to the model...")
        response = self.ctx.openai_client.responses.create(
            model="4.1",
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
            self.message_manager.add_ai_message(content=response.output_text)   
            action_result = ActionResult(action_result_msg="No action executed. The model output is text.", 
                                         success=True, 
                                         is_done=False)
        else:
            function_tool_call: ResponseFunctionToolCall = next((item for item in response.output if isinstance(item, ResponseFunctionToolCall)), None)
            if not function_tool_call:
                raise Exception(f"Step {step_number}, No function tool call or response message")
            
            logger.info(f"Step {step_number}, Function Tool Call:\n{function_tool_call.to_json()}")
            self.message_manager.add_ai_function_tool_call_message(function_tool_call=function_tool_call)
            
            action_result = await self.my_agent_tools.execute_tool(function_tool_call=function_tool_call)
            logger.info(f'Step {step_number}, Function Tool Call Result: {action_result.action_result_msg}')

            self.message_manager.add_tool_result_message(result_message=action_result.action_result_msg,
                                                         tool_call_id=function_tool_call.call_id)
                
        return action_result
