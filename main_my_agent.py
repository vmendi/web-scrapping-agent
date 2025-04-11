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
from my_agent import MyAgent

# logging.getLogger('browser_use').setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()


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

    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.0,
    )

    schools_task = f"""
Your goal is to get a list of all of the Harvard University's schools.
The output columns are:
    School Name,
    School Website URL.
"""
    
    try:
        agent = MyAgent(web_scrapping_task=schools_task, 
                        llm=llm, 
                        browser=browser, 
                        browser_context=browser_context,
                        context=None)
        await agent.run()
    finally:
        await browser.close()



asyncio.run(main())