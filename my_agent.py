import base64
import datetime
import json
import os
import logging
from typing import Generic, Optional, Type, TypeVar
from anthropic import Anthropic
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from browser_use import ActionModel, ActionResult, Agent, Browser, BrowserConfig, BrowserContextConfig, Controller
from browser_use.browser.context import BrowserContext
import asyncio
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, create_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, ToolMessage, AIMessage

logger = logging.getLogger(__name__)


class AgentOutputModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    evaluation_previous_goal: str
    memory: str
    next_goal: str
    action: ActionModel = Field(...,  description='Action to execute')
    
    @staticmethod
    def type_with_custom_action_model(custom_action_model: Type[ActionModel]) -> Type['AgentOutputModel']:
        model = create_model(
            'AgentOutputModel',
            __base__ = AgentOutputModel,
            action=(
                custom_action_model,
                Field(..., description='Action to execute'),
            ),
            __module__ = AgentOutputModel.__module__,
            __doc__ = 'AgentOutputModel model extended with a custom action model passed as parameter'
        )
        return model


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
    

Context = TypeVar('Context')

class MyAgent(Generic[Context]):
    def __init__(self, 
                 web_scrapping_task: str,
                 llm: BaseChatModel, 
                 browser: Browser, 
                 browser_context: BrowserContext,
                 context: Context,
                 ):
        self.web_scrapping_task = web_scrapping_task
        self.llm = llm
        self.browser = browser
        self.browser_context = browser_context
        self.context = context
        self.controller: Controller[Context]= Controller()
        self.system_message = self.get_system_message()
        self.message_manager = MessageManager(system_message=self.system_message)

        first_human_message = HumanMessage(content=f'Your Web Scrapping task is:\n"""{self.web_scrapping_task}"""')
        self.message_manager.add_human_message(message=first_human_message)
        
        custom_action_model = self.controller.registry.create_action_model()
        self.agent_output_model = AgentOutputModel.type_with_custom_action_model(custom_action_model=custom_action_model)

        self.openai_client = OpenAI()

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

        all_messages: list[BaseMessage] = self.message_manager.get_all_messages()

        structured_llm = self.llm.with_structured_output(schema=self.agent_output_model, 
                                                         include_raw=True,
                                                         method='function_calling')
        
        llm_output = await structured_llm.ainvoke(all_messages)
        agent_output_model = llm_output['parsed']

        logger.info(f"Step {step_number}, Agent output model:\n{agent_output_model.model_dump_json(indent=2, exclude_unset=True)}")
        self.message_manager.add_agent_model_output(agent_output_model=agent_output_model)

        await self.browser_context.remove_highlights()

        # ACT!
        action_result = await self.act(action=agent_output_model.action)
        logger.info(f'Action result: {action_result.extracted_content}')
        self.message_manager.add_action_result(action_result=action_result)
        

    async def act(self, action: ActionModel) -> ActionResult:
        action_result = await self.controller.act(
            action=action,
            browser_context=self.browser_context,
            page_extraction_llm=self.llm,
            sensitive_data=None,
            available_file_paths=None,
            context=self.context,
        )    
        return action_result


    async def multi_act(self, actions: list[ActionModel], check_for_new_elements: bool) -> list[ActionResult]:
        action_results = []

        cached_selector_map = await self.browser_context.get_selector_map()
        cached_path_hashes = set(e.hash.branch_path_hash for e in cached_selector_map.values())

        for i, action in enumerate(actions):
            # Hash all elements. if it is a subset of cached_state its fine - else there are new elements on page.
            # Inform for the moment. It was "break" in the original code.
            new_state = await self.browser_context.get_state()
            new_path_hashes = set(e.hash.branch_path_hash for e in new_state.selector_map.values())
            if check_for_new_elements and not new_path_hashes.issubset(cached_path_hashes):
                logger.info(f'Something new appeared after action {i} of {len(actions)}')
                
            action_result = await self.controller.act(
                action=action,
                browser_context=self.browser_context,
                page_extraction_llm=self.llm,
                sensitive_data=None,
                available_file_paths=None,
                context=self.context,
            )

            action_results.append(action_result)

            logger.info(f'Executed action {i + 1} of {len(actions)}')
            if action_results[-1].is_done or action_results[-1].error or i == len(actions) - 1:
                break

            await asyncio.sleep(self.browser_context.config.wait_between_actions)

        return action_results
        
        
        