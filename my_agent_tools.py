import asyncio
import json
import logging
import os
import csv
from tabulate import tabulate
from typing import Any, Awaitable, Callable, List, Optional
from functools import cached_property
from pydantic import BaseModel, Field
import datetime

from openai.types.responses import ResponseFunctionToolCall, Response
from agents.function_schema import function_schema
from agents import RunContextWrapper
from my_utils import MyAgentContext, format_json_pretty

logger = logging.getLogger(__name__)


class ActionResult(BaseModel):
    # The name of the action that was executed.
    action_name: str = "default_action_name"

    # Success from the point of view of the action, but we don't know if semantically it was successful or not. 
    # The agent will decide that by looking at the new current state after the action.
    success: bool = True

    # A message to return to the agent in either case of success or failure.
    action_result_msg: str = None           
    
    # Any kind of content that the action or agent wants to return to the caller
    content: Optional[dict[str, Any]] = None



async def done(ctx: RunContextWrapper[MyAgentContext], success: bool, message_to_user: str) -> ActionResult:
    """The agent has finished the task with success or failure, for whatever the reason.
    
    Args:
        success: bool - Whether the task is finished successfully or not.
        message_to_user: str - A message to return to the user in either case of success or failure.
    """	
    return ActionResult(action_name="done", action_result_msg=message_to_user, success=success)


async def search_google(ctx: RunContextWrapper[MyAgentContext], query: str) -> ActionResult:
    """Search the query in Google in the current tab.
    
    Args:
        query: str - The search query to use. Should be concrete and not vague or super long, like how humans search in Google. Focus on the single most important items.
    """
    page = await ctx.browser_context.get_current_page()
    await page.goto(f'https://www.google.com/search?q={query}&udm=14')
    await page.wait_for_load_state()
    return ActionResult(action_name="search_google", action_result_msg=f'Searched for "{query}" using Google', success=True)


async def go_back(ctx: RunContextWrapper[MyAgentContext]) -> ActionResult:
    """Navigate back to the previous page in the browser history of the current tab.
    """
    await ctx.browser_context.go_back()
    return ActionResult(action_name="go_back", action_result_msg='Navigated back', success=True)


async def go_to_url(ctx: RunContextWrapper[MyAgentContext], url: str) -> ActionResult:
    """Navigate to a specific URL in the current tab.
    
    Args:
        url: str - The URL to navigate to.
    """
    page = await ctx.browser_context.get_current_page()
    await page.goto(url)
    await page.wait_for_load_state()
    return ActionResult(action_name="go_to_url", action_result_msg=f'Navigated to {url}', success=True)


async def input_text(ctx: RunContextWrapper[MyAgentContext], index: int, text: str) -> ActionResult:
    """Input text into an interactive element identified by its index.
    
    Args:
        index: int - The index of the element to input text into.
        text: str - The text to input.
    """
    if index not in await ctx.browser_context.get_selector_map():
        raise Exception(f'Element index {index} does not exist - retry or use alternative actions')

    element_node = await ctx.browser_context.get_dom_element_by_index(index)
    await ctx.browser_context._input_text_element_node(element_node, text)

    return ActionResult(action_name="input_text", action_result_msg=f'Input {text} into index {index}', success=True)


async def click_element(ctx: RunContextWrapper[MyAgentContext], index: int) -> ActionResult:
    """Click on an element identified by its index in the current page.
    
    Args:
        index: int - The index of the element to click.
    """
    session = await ctx.browser_context.get_session()

    if index not in await ctx.browser_context.get_selector_map():
        raise Exception(f'Element with index {index} does not exist - retry or use alternative actions')

    element_node = await ctx.browser_context.get_dom_element_by_index(index)
    initial_pages = len(session.context.pages)

    # if element has file uploader then dont click
    if await ctx.browser_context.is_file_uploader(element_node):
        return ActionResult(action_name="click_element", 
                            action_result_msg="Index {index} - has an element which opens file upload dialog. " +
                                              "To upload files please use a specific function to upload files", 
                            success=False)

    try:
        download_path = await ctx.browser_context._click_element_node(element_node)

        if download_path:
            msg = f"Downloaded file to {download_path}"
        else:
            msg = f"Clicked button with index {index}: {element_node.get_all_text_till_next_clickable_element(max_depth=2)}"

        if len(session.context.pages) > initial_pages:
            new_tab_msg = 'New tab opened - switching to it'
            msg += f' - {new_tab_msg}'
            await ctx.browser_context.switch_to_tab(-1)
        
        return ActionResult(action_name="click_element", action_result_msg=msg, success=True)
    except Exception as e:
        return ActionResult(
            action_name="click_element", 
            action_result_msg=f"Element not clickable with index {index} - most likely the page changed. Exception\n: {str(e)}",
            success=False)


async def open_tab(ctx: RunContextWrapper[MyAgentContext], url: str) -> ActionResult:
    """Open a new tab with the specified URL.
    
    Args:
        url: str - The URL to open in the new tab.
    """
    await ctx.browser_context.create_new_tab(url)

    return ActionResult(action_name="open_tab", action_result_msg=f'Opened new tab with {url}', success=True)


async def switch_tab(ctx: RunContextWrapper[MyAgentContext], page_id: int) -> ActionResult:
    """Switch to a specific tab by its ID.
    
    Args:
        page_id: int - The ID of the tab to switch to.
    """
    await ctx.browser_context.switch_to_tab(page_id)
    # Wait for tab to be ready
    page = await ctx.browser_context.get_current_page()
    await page.wait_for_load_state()
    
    return ActionResult(action_name="switch_tab", action_result_msg=f'Switched to tab {page_id}', success=True)


async def scroll_down(ctx: RunContextWrapper[MyAgentContext], amount: int) -> ActionResult:
    """Scroll down the page by a specified amount of pixels.
    
    Args:
        amount: int - The number of pixels to scroll down. If 0, scrolls down one page height.
    """
    page = await ctx.browser_context.get_current_page()
    
    if amount != 0:
        await page.evaluate(f'window.scrollBy(0, {amount});')
    else:
        await page.evaluate('window.scrollBy(0, window.innerHeight);')

    amount_str = f'{amount} pixels' if amount != 0 else 'one page'

    return ActionResult(action_name="scroll_down", action_result_msg=f'Scrolled down the page by {amount_str}', success=True)


async def scroll_up(ctx: RunContextWrapper[MyAgentContext], amount: int) -> ActionResult:
    """Scroll up the page by a specified amount of pixels.
    
    Args:
        amount: int - The number of pixels to scroll up. If 0, scrolls up one page height.
    """
    page = await ctx.browser_context.get_current_page()

    if amount != 0:
        await page.evaluate(f'window.scrollBy(0, -{amount});')
    else:
        await page.evaluate('window.scrollBy(0, -window.innerHeight);')

    amount_str = f'{amount} pixels' if amount != 0 else 'one page'

    return ActionResult(action_name="scroll_up", action_result_msg=f'Scrolled up the page by {amount_str}', success=True)


async def send_keys(ctx: RunContextWrapper[MyAgentContext], keys: str) -> ActionResult:
    """Send strings of special keys like Escape, Backspace, Insert, PageDown, Delete, Enter, or shortcuts like 'Control+o', 'Control+Shift+T'.
    
    Args:
        keys: str - The keys to send. Can be special keys or keyboard shortcuts.
    """
    page = await ctx.browser_context.get_current_page()
    try:
        await page.keyboard.press(keys)
    except Exception as e:
        if 'Unknown key' in str(e):
            for key in keys:    # loop over the keys and try to send each one
                try:
                    await page.keyboard.press(key)
                except Exception as e:
                    logger.error(f'Error sending key {key}: {str(e)}')
                    raise e
        else:
            raise e
    
    return ActionResult(action_name="send_keys", action_result_msg=f'Sent keys: {keys}', success=True)


async def scroll_to_text(ctx: RunContextWrapper[MyAgentContext], text: str) -> ActionResult:
    """Scroll to a specific text on the page.
    
    Args:
        text: str - The text to scroll to.
    """
    page = await ctx.browser_context.get_current_page()
    try:
        # Try different locator strategies
        locators = [
            page.get_by_text(text, exact=False),
            page.locator(f'text={text}'),
            page.locator(f"//*[contains(text(), '{text}')]"),
        ]

        for locator in locators:
            try:
                # First check if element exists and is visible
                if await locator.count() > 0 and await locator.first.is_visible():
                    await locator.first.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)  # Wait for scroll to complete
                    return ActionResult(action_name="scroll_to_text", 
                                        action_result_msg=f'Scrolled to text: {text}',
                                        success=True)
            except Exception as e:
                logger.error(f'Locator attempt failed: {str(e)}')
                continue

        return ActionResult(action_name="scroll_to_text", 
                            action_result_msg=f"Text '{text}' not found or not visible on page", 
                            success=False)

    except Exception as e:
        return ActionResult(action_name="scroll_to_text", 
                            action_result_msg=f"Failed to scroll to text '{text}': {str(e)}", 
                            success=False)


async def get_dropdown_options(ctx: RunContextWrapper[MyAgentContext], index: int) -> ActionResult:
    """Get all options from a native dropdown.
    
    Args:
        index: int - The index of the dropdown element.
    """
    page = await ctx.browser_context.get_current_page()
    selector_map = await ctx.browser_context.get_selector_map()
    dom_element = selector_map[index]

    try:
        # Frame-aware approach since we know it works
        all_options = []
        frame_index = 0

        for frame in page.frames:
            try:
                options = await frame.evaluate(
                    """
                    (xpath) => {
                        const select = document.evaluate(xpath, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (!select) return null;

                        return {
                            options: Array.from(select.options).map(opt => ({
                                text: opt.text, //do not trim, because we are doing exact match in select_dropdown_option
                                value: opt.value,
                                index: opt.index
                            })),
                            id: select.id,
                            name: select.name
                        };
                    }
                """,
                    dom_element.xpath,
                )

                if options:
                    logger.debug(f'Found dropdown in frame {frame_index}')
                    logger.debug(f'Dropdown ID: {options["id"]}, Name: {options["name"]}')

                    formatted_options = []
                    for opt in options['options']:
                        # encoding ensures AI uses the exact string in select_dropdown_option
                        encoded_text = json.dumps(opt['text'])
                        formatted_options.append(f'{opt["index"]}: text={encoded_text}')

                    all_options.extend(formatted_options)

            except Exception as frame_e:
                logger.error(f'Frame {frame_index} evaluation failed: {str(frame_e)}')

            frame_index += 1

        if all_options:
            msg = '\n'.join(all_options)
            msg += '\nUse the exact text string in select_dropdown_option'
            return ActionResult(action_name="get_dropdown_options", action_result_msg=msg, success=True)
        else:
            msg = 'No options found in any frame for dropdown'
            return ActionResult(action_name="get_dropdown_options", action_result_msg=msg, success=False)

    except Exception as e:
        return ActionResult(action_name="get_dropdown_options", action_result_msg=f'Error getting options: {str(e)}', success=False)


async def select_dropdown_option(ctx: RunContextWrapper[MyAgentContext], index: int, text: str) -> ActionResult:
    """Select the option 'text' in the dropdown interactive element identified as 'index'.
    
    Args:
        index: int - The index of the dropdown element.
        text: str - The text of the option to select.
    """
    page = await ctx.browser_context.get_current_page()
    selector_map = await ctx.browser_context.get_selector_map()
    dom_element = selector_map[index]

    # Validate that we're working with a select element
    if dom_element.tag_name != 'select':
        return ActionResult(
            action_name="select_dropdown_option", 
            action_result_msg=f'Cannot select option: Element with index {index} is a {dom_element.tag_name}, not a select',
            success=False)

    logger.debug(f"Attempting to select '{text}' using xpath: {dom_element.xpath}")
    logger.debug(f'Element attributes: {dom_element.attributes}')
    logger.debug(f'Element tag: {dom_element.tag_name}')

    xpath = '//' + dom_element.xpath

    try:
        frame_index = 0
        for frame in page.frames:
            try:
                logger.debug(f'Trying frame {frame_index} URL: {frame.url}')

                # First verify we can find the dropdown in this frame
                find_dropdown_js = """
                    (xpath) => {
                        try {
                            const select = document.evaluate(xpath, document, null,
                                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                            if (!select) return null;
                            if (select.tagName.toLowerCase() !== 'select') {
                                return {
                                    error: `Found element but it's a ${select.tagName}, not a SELECT`,
                                    found: false
                                };
                            }
                            return {
                                id: select.id,
                                name: select.name,
                                found: true,
                                tagName: select.tagName,
                                optionCount: select.options.length,
                                currentValue: select.value,
                                availableOptions: Array.from(select.options).map(o => o.text.trim())
                            };
                        } catch (e) {
                            return {error: e.toString(), found: false};
                        }
                    }
                """

                dropdown_info = await frame.evaluate(find_dropdown_js, dom_element.xpath)

                if dropdown_info:
                    if not dropdown_info.get('found'):
                        logger.error(f'Frame {frame_index} error: {dropdown_info.get("error")}')
                        continue

                    logger.debug(f'Found dropdown in frame {frame_index}: {dropdown_info}')

                    # "label" because we are selecting by text
                    # nth(0) to disable error thrown by strict mode
                    # timeout=1000 because we are already waiting for all network events, therefore ideally we don't need to wait a lot here (default 30s)
                    selected_option_values = (
                        await frame.locator('//' + dom_element.xpath).nth(0).select_option(label=text, timeout=1000)
                    )

                    return ActionResult(action_name="select_dropdown_option", 
                                        action_result_msg=f'Selected option {text} with value {selected_option_values}', 
                                        success=True)

            except Exception as frame_e:
                logger.error(f'Frame {frame_index} attempt failed: {str(frame_e)}')
                logger.error(f'Frame type: {type(frame)}')
                logger.error(f'Frame URL: {frame.url}')

            frame_index += 1

        return ActionResult(action_name="select_dropdown_option", 
                            action_result_msg=f"Could not select option '{text}' in any frame", 
                            success=False)

    except Exception as e:
        return ActionResult(action_name="select_dropdown_option", 
                            action_result_msg=f'Selection failed: {str(e)}',
                            success=False)


class VisitedUrl(BaseModel):
    url: str = Field(..., description="The URL that was visited.")
    relevant: bool = Field(..., description="Whether the URL was relevant to the navigation goal or not.")
    reason: str = Field(..., description="Why the URL was relevant or not to the navigation goal.")

class NavigationDoneResult(BaseModel):
    """The result of the navigation task as reported by the navigator agent."""
    success: bool = Field(..., description="Whether the navigation was successful or not.")
    status_message: str = Field(..., description="A message to return to the user in either case of success or failure.")
    visited_urls: list[VisitedUrl] = Field(..., description="A list of URLs that were visited, whether they were relevant to the navigation goal or not, and why.")

async def navigation_done(ctx: RunContextWrapper[MyAgentContext], navigation_done_result: NavigationDoneResult) -> ActionResult:
    """The navigator has finished the task with success or failure, for whatever the reason.
    """	
    return ActionResult(action_name="navigation_done",
                        action_result_msg=navigation_done_result.model_dump_json(), 
                        success=navigation_done_result.success)


async def wna_navigate_and_find(ctx: RunContextWrapper[MyAgentContext], navigation_goal: str) -> ActionResult:
    """Invoke the Web Navigation Agent (WNA) to autonomously browse the web until the navigation goal is satisfied.

    Args:
        navigation_goal: str - A concise, natural-language description of what the navigator should accomplish.
    """
    from my_navigator_agent import MyNavigatorAgent  # local import to avoid circular dependency
    
    navigator = MyNavigatorAgent(ctx=ctx.new_agent_context(), 
                                 navigation_goal=navigation_goal)
    
    action_result: ActionResult = await navigator.run()

    return ActionResult(action_name="wna_navigate_and_find", 
                        action_result_msg=action_result.action_result_msg, 
                        success=action_result.success)


async def cea_extract_content(ctx: RunContextWrapper[MyAgentContext], extraction_goal: str, row_schema: str) -> ActionResult:
    """Invoke the Content Extraction Agent (CEA) to scrape structured data from a page.

    Args:    
        extraction_goal: str - A natural language description of what information should be extracted.
        row_schema: str - A simplified schema in JSON format describing the *shape of a single row* of the table that should be extracted. Use `snake_case` keys. Example:
            ```json
            {
                "some_name": "string",
                "the_age": "integer"
            }
            ```
    """
    from my_content_extract_agent import MyContentExtractAgent
    agent = MyContentExtractAgent(ctx=ctx.new_agent_context(), 
                                  extraction_goal=extraction_goal, 
                                  row_schema=row_schema)
    
    action_result: ActionResult = await agent.run()

    return ActionResult(action_name="cea_extract_content", 
                        action_result_msg=action_result.action_result_msg, 
                        success=action_result.success)
    

class PlanStep(BaseModel):
    step_id: int
    goal: str
    success_criteria: str
    is_done: bool

class Plan(BaseModel):
    plan: list[PlanStep]


async def persist_plan(ctx: RunContextWrapper[MyAgentContext], plan: Plan) -> ActionResult:
    """Persist the plan to memory.

    Args:
        plan: Plan - The plan to persist.
    """
    ctx.memory["plan"] = plan

    return ActionResult(action_name="persist_plan", 
                        action_result_msg=f"Plan persisted to memory",
                        success=True)


async def print_file_content(ctx: RunContextWrapper[MyAgentContext], file_path: str) -> ActionResult:
    """Reads the content of a file and prints it into our conversation.
    Supported extensions: .csv, .json. Other extensions will be treated as plain text.

    Args:
        file_path: str - The path to the file to read.
    """
    try:
        _, file_extension = os.path.splitext(file_path)
        file_extension = file_extension.lower()

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        formatted_content = f"Content of file '{file_path}':\n"

        if file_extension == '.csv':
            reader = csv.reader(content.splitlines())
            rows = list(reader)
            if not rows:
                formatted_content += "(empty CSV)"
            else:
                # Use tabulate for better table formatting
                headers = rows[0] if rows else []
                table_data = rows[1:] if len(rows) > 1 else []
                formatted_content += tabulate(table_data, headers=headers, tablefmt="grid")
        elif file_extension == '.json':
            try:
                parsed_json = json.loads(content)
                formatted_content += json.dumps(parsed_json, indent=2)
            except json.JSONDecodeError as e:
                formatted_content += f"Error decoding JSON: {e}\nRaw content:\n{content}"
        else: # Plain text or other
            formatted_content += content

        return ActionResult(action_name="print_file_content", action_result_msg=formatted_content, success=True)

    except FileNotFoundError:
        return ActionResult(action_name="print_file_content", 
                            action_result_msg=f"Error: File not found at '{file_path}'",
                            success=False)
    except Exception as e:
        return ActionResult(action_name="print_file_content", 
                            action_result_msg=f"Error reading or formatting file '{file_path}': {str(e)}", 
                            success=False)


async def persist_rows(ctx: RunContextWrapper[MyAgentContext], rows: list[str]) -> ActionResult:
    """Persist rows of data that conform to the provided schema.
    
    Args:
        rows: list[str] - A list of strings where each string can be parsed as a JSON object that conforms to the schema provided.
    """
    if not rows:
        return ActionResult(action_name="persist_rows", 
                           action_result_msg="No rows were passed to the tool!", 
                           success=False)
    
    if "extracted_rows" not in ctx.memory:
        ctx.memory["extracted_rows"] = []
    
    ctx.memory["extracted_rows"].extend(rows)
    
    return ActionResult(action_name="persist_rows", 
                        action_result_msg=f"Successfully persisted {len(rows)} rows. Total rows: {len(ctx.memory['extracted_rows'])}", 
                        success=True)


async def extraction_done(ctx: RunContextWrapper[MyAgentContext], status: bool, status_message: str) -> ActionResult:
    """Signal that the content extraction is complete and persist the final results.
    
    Args:
        status: bool - Whether the extraction was successful or not.
        status_message: str - A summary of actions taken on success, or an explanation of why it was not possible to accomplish the goal on failure.
    """
    extracted_rows = ctx.memory.get("extracted_rows", [])
    
    if extracted_rows:
        try:
            fieldnames = list(extracted_rows[0].keys())
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"extracted_data_{timestamp}.csv"
            
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(extracted_rows)
                
            status_message = f"{status_message}\nData saved to {csv_filename}"
        except Exception as e:
            status_message = f"{status_message}\nError saving to CSV: {str(e)}"
            status = False
        
    return ActionResult(
        action_name="extraction_done",
        action_result_msg=f"Extraction complete. Status: {'Success' if status else 'Failed'}. {status_message}\nTotal rows extracted {len(extracted_rows)}",
        success=status,
        content={'extracted_rows': extracted_rows, 'csv_path': csv_filename}
    )


class MyAgentTools:
    def __init__(self, ctx: MyAgentContext, tools: List[Callable[[RunContextWrapper[MyAgentContext], Any], Awaitable[ActionResult]]]):
        self.ctx = ctx
        self.tools = list(tools)

    def get_tools(self):
        return self.tools

    @cached_property
    def tools_schema(self) -> list[dict]:
        tools_schema: list[dict] = []
        for tool in self.tools:
            function_tool_schema = function_schema(tool)
            tools_schema.append({
                "type": "function",
                "name": function_tool_schema.name,
                "parameters": function_tool_schema.params_json_schema,
                "strict": function_tool_schema.strict_json_schema,
                "description": function_tool_schema.description,
            })
        return tools_schema

    async def execute_tool(self, function_tool_call: ResponseFunctionToolCall) -> ActionResult:
        tool_name = function_tool_call.name
        tool = next((t for t in self.tools if t.__name__ == tool_name), None)
        if not tool:
            return ActionResult(action_name="execute_tool", action_result_msg=f"Tool '{tool_name}' not found", success=False)

        tool_args = json.loads(function_tool_call.arguments)
        return await tool(self.ctx, **tool_args)

    async def handle_tool_call(self, current_step: int, response: Response, message_manager: Any) -> ActionResult:
        tool_call_generator = (item for item in response.output if isinstance(item, ResponseFunctionToolCall))
        function_tool_call: ResponseFunctionToolCall = next(tool_call_generator, None)

        if not function_tool_call:
            raise RuntimeError('No function tool call detected')
            
        logger.info(f"Step {current_step}, Function Tool Call:\n{format_json_pretty(function_tool_call.to_json())}")
        message_manager.add_ai_function_tool_call_message(function_tool_call=function_tool_call,
                                                          ephemeral=False)

        action_result = await self.execute_tool(function_tool_call=function_tool_call)
        logger.info(f'Step {current_step}, Function Tool Call Result:\n{format_json_pretty(action_result.model_dump_json())}')
        message_manager.add_tool_result_message(result_message=action_result.action_result_msg,
                                                tool_call_id=function_tool_call.call_id,
                                                ephemeral=False)
        return action_result


BRAIN_TOOLS: List[Callable[[RunContextWrapper[MyAgentContext], Any], Awaitable[ActionResult]]] = [
    done,
    wna_navigate_and_find,
    cea_extract_content,
    print_file_content,
    # persist_plan,
]

NAVIGATOR_TOOLS: List[Callable[[RunContextWrapper[MyAgentContext], Any], Awaitable[ActionResult]]] = [
    done,
    search_google,
    go_back,
    go_to_url,
    click_element,
    # input_text,
    # send_keys,    
    open_tab,
    switch_tab,
    # scroll_down,
    # scroll_up,
    # scroll_to_text,
    get_dropdown_options,
    select_dropdown_option,
]

CEA_TOOLS: List[Callable[[RunContextWrapper[MyAgentContext], Any], Awaitable[ActionResult]]] = [
    go_back,
    go_to_url,
    click_element,
    input_text,
    send_keys,
    get_dropdown_options,
    select_dropdown_option,
    extraction_done,
    persist_rows,
]


class MyBrainAgentTools(MyAgentTools):
    def __init__(self, ctx: MyAgentContext):
        super().__init__(ctx, tools=BRAIN_TOOLS)


class MyNavigatorAgentTools(MyAgentTools):
    def __init__(self, ctx: MyAgentContext):
        super().__init__(ctx, tools=NAVIGATOR_TOOLS)


class MyContentExtractAgentTools(MyAgentTools):
    def __init__(self, ctx: MyAgentContext):
        super().__init__(ctx, tools=CEA_TOOLS)