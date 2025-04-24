import logging
import datetime
import os
from browser_use import Browser, BrowserConfig, BrowserContextConfig
import asyncio
from dotenv import load_dotenv
from openai import OpenAI
from my_navigator_agent import MyNavigatorAgent
from my_planner_agent import MyPlannerAgent
from my_utils import MyAgentContext

logger = logging.getLogger(__name__)

load_dotenv()

def list_available_openai_models():
    client = OpenAI()
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

    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir = f"output/{run_id}/"
    os.makedirs(save_dir, exist_ok=True)

    openai_client = OpenAI()

    ctx = MyAgentContext(browser_context=browser_context, 
                         openai_client=openai_client, 
                         save_dir=save_dir,
                         run_id=run_id)
    
    try:
        # agent = MyNavigatorAgent(ctx=ctx)
        agent = MyPlannerAgent(ctx=ctx)
        await agent.run()
    finally:
        if browser_context:
            await browser_context.close()
        if browser:
            await browser.close()


# list_available_openai_models()
asyncio.run(main())