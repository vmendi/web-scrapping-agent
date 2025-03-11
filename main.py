import base64
import datetime
import json
import os
from anthropic import Anthropic
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser, BrowserConfig, BrowserContextConfig, Controller
import asyncio
from dotenv import load_dotenv
import openai
from pydantic import BaseModel
from langchain_core.language_models import BaseChatModel

# import logging
# logging.getLogger('browser_use').setLevel(logging.DEBUG)

load_dotenv()

async def list_available_openai_models():
    # Create an OpenAI client using the API key from environment variables
    client = openai.OpenAI()
    
    # List available models
    models = client.models.list()
    
    # Print model IDs
    print("Available models:")
    for model in models.data:
        print(f"- {model.id}")


async def list_available_anthropic_models():
    client = Anthropic()
    models = client.models.list()
    print("Available models:")
    for model in models.data:
        print(f"- {model.id}")


class Department(BaseModel):
    department_name: str
    department_website_url: str
    

class DepartmentList(BaseModel):
    school_name: str
    departments: list[Department]


class School(BaseModel):
    school_name: str
    school_website_url: str


class SchoolList(BaseModel):
    university_name: str
    schools: list[School]




def to_valid_filename(s: str):
    return s.lower().replace(" ", "_").replace(".", "").replace("'", "").replace(",", "")
    

def save_history(history, log_dir, prefix):
    from history_logger import save_history_to_disk, print_history_summary    
    save_history_to_disk(history, log_dir, prefix)
    print_history_summary(history)


def recreate_log_dir(log_dir):
    import shutil
    if os.path.exists(log_dir):
        shutil.rmtree(log_dir) 
    os.makedirs(log_dir, exist_ok=True)



def configure_departments_controller():
    controller = Controller()

    @controller.action('save_departments: Save a list of departments to a file', param_model=DepartmentList)
    def save_departments(department_list: DepartmentList):
        departments_file = f"out/departments_{to_valid_filename(department_list.school_name)}.json"

        print(f"save_departments: {department_list.model_dump_json(indent=2)}")
    
        with open(departments_file, "w") as f:
            f.write(department_list.model_dump_json(indent=2))
    
    return controller


def configure_schools_controller():
    controller = Controller()
    
    @controller.action('save_schools: Save a list of all the schools to a file.', param_model=SchoolList)
    def save_schools(school_list: SchoolList):
        schools_file = f"out/schools.json"

        print(f"save_schools: {school_list.model_dump_json(indent=2)}")

        with open(schools_file, "w") as f:
            f.write(school_list.model_dump_json(indent=2))

    return controller



async def main():
    browser_context_config = BrowserContextConfig(
        viewport_expansion=-1,     # -1 to include all elements
        maximum_wait_page_load_time=5,
        minimum_wait_page_load_time=0.5,
        wait_between_actions=0.5,
        disable_security=True,
        highlight_elements=True
    )

    # config = BrowserConfig(
    #     chrome_instance_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    #     new_context_config=browser_context_config
    # )

    # config = BrowserConfig(
    #     headless=False,
    #     disable_security=True,
    #     extra_chromium_args=["--window-position=-1300,0"],
    #     new_context_config=browser_context_config
    #     # wss_url="ws://localhost:50216/",
    # )

    config = BrowserConfig(
        cdp_url="http://127.0.0.1:9223",
        new_context_config=browser_context_config
    )

    browser = Browser(config=config)
    
    llm = ChatOpenAI(
        # model="o1",
        model="gpt-4o-mini",
        temperature=0.0,
    )
    
    planner_llm = ChatAnthropic(
        model_name="claude-3-7-sonnet-20250219",
        temperature=0.0
    )
    
    log_dir = f"logs/current"
    recreate_log_dir(log_dir)
    os.makedirs("out", exist_ok=True)

    try:
        await run_all_tasks(browser=browser, 
                            llm=llm, 
                            planner_llm=planner_llm, 
                            log_dir=log_dir)
    finally:
        await browser.close()


async def run_all_tasks(browser: Browser, llm: BaseChatModel, planner_llm: BaseChatModel, log_dir: str):   
    schools = await run_task_fetch_schools(browser=browser, 
                                          llm=llm, 
                                          planner_llm=planner_llm, 
                                          log_dir=log_dir)
    
    await run_task_fetch_departments_for_all_schools(browser=browser, 
                                                    llm=llm, 
                                                    planner_llm=planner_llm, 
                                                    log_dir=log_dir, 
                                                    schools=schools)


async def run_task_fetch_schools(browser, llm, planner_llm, log_dir) -> SchoolList:
    # If the schools file already exists, return it
    if os.path.exists(f"out/schools.json"):
        with open(f"out/schools.json", "r") as f:
            schools = json.load(f)
        return SchoolList(**schools)

    schools_task = f"""
    Your goal is to get a list of all Harvard University's schools.
    The output columns are:
        School Name,
        School Website URL.
    If you need to navigate to a school page, open a new tab so that you can keep the tab with the schools list open and come back later.
    Persist the list of schools to file using the save_schools action.
    """

    schools_agent = Agent(
        task=schools_task,
        llm=llm,
        planner_llm=planner_llm,
        controller=configure_schools_controller(),
        use_vision=True,
        use_vision_for_planner=False,
        browser=browser,
        save_conversation_path=f"{log_dir}/schools.json"
    )
    history = await schools_agent.run(max_steps=10000)
    save_history(history, log_dir, prefix="schools")

    # The agent persists to file, so to return the list of schools we simply read from it
    if os.path.exists(f"out/schools.json"):
        with open(f"out/schools.json", "r") as f:
            schools = json.load(f)
    else:
        raise Exception("Schools file not found")

    return SchoolList(**schools)


async def run_task_fetch_departments_for_all_schools(browser, llm, planner_llm, log_dir, schools: SchoolList):
    for school in schools.schools:
        if school.school_name == 'Harvard Faculty of Arts and Sciences': # 'Harvard Law School'
            await run_task_fetch_departments_for_school(browser=browser, 
                                                        llm=llm, 
                                                        planner_llm=planner_llm, 
                                                        log_dir=log_dir, 
                                                        school=school)
            break

    # for school in schools.schools:
    #     await run_task_department(browser, llm, log_dir, school)


async def run_task_fetch_departments_for_school(browser, llm, planner_llm, log_dir, school: School):
    # extend_system_message = f"""
    #     You are a Web Scrapper bot in charge of scraping Department information from a university website. Accuracy and completeness are of the utmost importance.
    # """

    school_task = f"""
    Get a list of all the departments for the school {school.school_name} of Harvard University.

    Use the search results from Google only to get the URLs of the school or any departments, but
    never to extract departments themselvels. 

    Try to find a single page where all departments are listed.
   
    The output columns are:
        Department Name,
        Department Website URL.

    Persist the list of departments to file using the save_departments action.
    """

    agent = Agent(
        task=school_task,
        llm=llm,
        planner_llm=planner_llm,
        controller=configure_departments_controller(),
        use_vision=True,
        use_vision_for_planner=True,
        browser=browser,
        save_conversation_path=f"{log_dir}/departments_{to_valid_filename(school.school_name)}.json"
    )
    history = await agent.run(max_steps=10000)
    save_history(history, log_dir, prefix=f"departments_{to_valid_filename(school.school_name)}")


# asyncio.run(list_available_openai_models())
# asyncio.run(list_available_anthropic_models())
asyncio.run(main())