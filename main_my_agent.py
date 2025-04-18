import base64
import logging
import datetime
import json
import os
from typing import Generic, Optional, Type, TypeVar
from anthropic import Anthropic
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from browser_use import ActionModel, ActionResult, Agent, Browser, BrowserConfig, BrowserContextConfig, Controller
from browser_use.browser.context import BrowserContext
import asyncio
from dotenv import load_dotenv
import openai
from pydantic import BaseModel, ConfigDict, Field, create_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, ToolMessage, AIMessage
from my_navigator_agent import MyNavigatorAgent
from my_planner_agent import MyPlannerAgent

# logging.getLogger('browser_use').setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

def list_available_openai_models():
    client = openai.OpenAI()
    models = client.models.list()
    print("Available models:")
    for model in models.data:
        print(f"- {model.id}")


async def main():
    browser_context_config = BrowserContextConfig(
        viewport_expansion=-1,                      # -1 to include all elements
        maximum_wait_page_load_time=5,              # default is 5 seconds
        minimum_wait_page_load_time=0.5,            # default is 0.5 seconds
        wait_between_actions=0.5,                   # default is 0.5 seconds
        wait_for_network_idle_page_load_time=1,     # default is 1 seconds
        disable_security=True,
        highlight_elements=True
    )

    config = BrowserConfig(
        cdp_url="http://127.0.0.1:9223",
        new_context_config=browser_context_config
    )

    browser = Browser(config=config)
    browser_context = await browser.new_context(config=browser_context_config)
    
    try:
        agent = MyNavigatorAgent(browser=browser, 
                                 browser_context=browser_context)
        # agent = MyPlannerAgent(browser=browser, 
        #                        browser_context=browser_context)
        await agent.run()
    finally:
        if browser_context:
            await browser_context.close()
        if browser:
            await browser.close()


# list_available_openai_models()
asyncio.run(main())