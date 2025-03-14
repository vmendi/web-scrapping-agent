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

import logging
logging.getLogger('browser_use').setLevel(logging.DEBUG)

load_dotenv()



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



async def main():
    # extend_system_message = f"""
    #     You are a Web Scrapper bot in charge of scraping Department information from a university website. Accuracy and completeness are of the utmost importance.
    # """
    browser_context_config = BrowserContextConfig(
        viewport_expansion=-1,              # -1 to include all elements
        maximum_wait_page_load_time=5,      # default is 5 seconds
        minimum_wait_page_load_time=0.5,    # default is 0.5 seconds
        wait_between_actions=0.5,           # default is 0.5 seconds
        wait_for_network_idle_page_load_time=1,   # default is 1 seconds
        disable_security=True,
        highlight_elements=True
    )

    config = BrowserConfig(
        cdp_url="http://127.0.0.1:9223",
        new_context_config=browser_context_config
    )

    browser = Browser(config=config)
        
    llm = ChatOpenAI(
        # model="o1",
        # model="gpt-4o-mini",
        # model="gpt-4o",
        # model="computer-use-preview",
        # model="gpt-4.5-preview",
        model="gpt-4o-mini",
        temperature=0.0,
    )
    
    # planner_llm = ChatAnthropic(
    #     model_name="claude-3-7-sonnet-20250219",
    #     temperature=0.0
    # )

    planner_llm = ChatOpenAI(
        # model="o1"
        model="gpt-4o-mini",
        temperature=0.0,
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
    
    departments = []
    for school in schools.schools:
        if school.school_name == 'Harvard Faculty of Arts and Sciences': # 'Harvard Law School'
            departments = await run_task_fetch_departments_for_school(browser=browser, 
                                                                      llm=llm, 
                                                                      planner_llm=planner_llm, 
                                                                      log_dir=log_dir, 
                                                                      school=school)
            break

    # for school in schools.schools:
    #     await run_task_department(browser, llm, log_dir, school)

    for department in departments.departments:
         courses = await run_task_fetch_courses_for_department(browser=browser, 
                                                               llm=llm, 
                                                               planner_llm=planner_llm, 
                                                               log_dir=log_dir, 
                                                               school=school, 
                                                               department=department)


async def run_task_fetch_schools(browser, llm, planner_llm, log_dir) -> SchoolList:
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



async def run_task_fetch_departments_for_school(browser, llm, planner_llm, log_dir, school: School):
    if os.path.exists(f"out/departments_{to_valid_filename(school.school_name)}.json"):
        with open(f"out/departments_{to_valid_filename(school.school_name)}.json", "r") as f:
            departments = json.load(f)
        return DepartmentList(**departments)

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
    
    save_history(history=history, 
                 log_dir=log_dir, 
                 prefix=f"departments_{to_valid_filename(school.school_name)}")
    
    if os.path.exists(f"out/departments_{to_valid_filename(school.school_name)}.json"):
        with open(f"out/departments_{to_valid_filename(school.school_name)}.json", "r") as f:
            departments = json.load(f)
    else:
        raise Exception("Departments file not found")
    
    return DepartmentList(**departments)


async def run_task_fetch_courses_for_department(browser, llm, planner_llm, log_dir, school: School, department: Department):
    department_task = f"""
    Get a list of all the courses for the department '{department.department_name}' of the school '{school.school_name}'.
    The output columns are:
        Course Name,
        Course Description,
        Course Code,
        Course Term.
    Try to find a single page where all courses are listed.
    The academic year we want is 2024-2025.
    The department might not offer courses for that year, in that case just return an empty list.
    Persist the list of courses to file using the save_courses action.
    """

    agent = Agent(
        task=department_task,
        llm=llm,
        planner_llm=planner_llm,
        controller=configure_courses_controller(),
        use_vision=True,
        use_vision_for_planner=True,
        browser=browser,
        browser_context=await browser.new_context(config=browser.config.new_context_config),
        save_conversation_path=f"{log_dir}/courses_{to_valid_filename(department.department_name)}.json"
    )

    history = await agent.run(max_steps=10000)

    save_history(history=history, 
                log_dir=log_dir, 
                prefix=f"courses_{to_valid_filename(department.department_name)}")

# asyncio.run(list_available_openai_models())
# asyncio.run(list_available_anthropic_models())
asyncio.run(main())