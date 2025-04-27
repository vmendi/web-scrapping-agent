import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, List, Optional
from functools import cached_property
from pydantic import BaseModel

from openai.types.responses import ResponseFunctionToolCall
from agents.function_schema import function_schema
from agents import RunContextWrapper
from my_content_extract_agent import MyContentExtractAgent
from my_utils import MyAgentContext

logger = logging.getLogger(__name__)


class ActionResult(BaseModel):
    # Success from the point of view of the action, but we don't know if semantically it was successful or not. 
    # The agent will decide that by looking at the new current state after the action.
    success: bool = True

    # A message to return to the agent in either case of success or failure.
    action_result_msg: str = None           
    
    # Signals that it was the last step for the agent.
    is_done: Optional[bool] = False



async def done(ctx: RunContextWrapper[MyAgentContext], success: bool, message_to_user: str) -> ActionResult:
    """The agent has finished the task with success or failure, for whatever the reason.
    
    Args:
        success: bool - Whether the task is finished successfully or not.
        message_to_user: str - A message to return to the user in either case of success or failure.
        
    Returns:
        ActionResult - The result of the action
    """	
    return ActionResult(action_result_msg=message_to_user, 
                        success=success, 
                        is_done=True)


async def search_google(ctx: RunContextWrapper[MyAgentContext], query: str) -> ActionResult:
    """Search the query in Google in the current tab.
    
    Args:
        query: str - The search query to use. Should be concrete and not vague or super long, like how humans search in Google. Focus on the single most important items.
        
    Returns:
        ActionResult - The result of the action with the search confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    await page.goto(f'https://www.google.com/search?q={query}&udm=14')
    await page.wait_for_load_state()
    return ActionResult(action_result_msg=f'Searched for "{query}" using Google',
                        success=True)


async def go_back(ctx: RunContextWrapper[MyAgentContext]) -> ActionResult:
    """Navigate back to the previous page in the browser history of the current tab.
            
    Returns:
        ActionResult - The result of the action with the navigation confirmation message
    """
    await ctx.browser_context.go_back()
    return ActionResult(action_result_msg='Navigated back', 
                        success=True)


async def go_to_url(ctx: RunContextWrapper[MyAgentContext], url: str) -> ActionResult:
    """Navigate to a specific URL in the current tab.
    
    Args:
        url: str - The URL to navigate to.

    Returns:
        ActionResult - The result of the action with the navigation confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    await page.goto(url)
    await page.wait_for_load_state()
    return ActionResult(action_result_msg=f'Navigated to {url}', 
                        success=True)


async def input_text(ctx: RunContextWrapper[MyAgentContext], index: int, text: str) -> ActionResult:
    """Input text into an interactive element identified by its index.
    
    Args:
        index: int - The index of the element to input text into.
        text: str - The text to input.
        
    Returns:
        ActionResult - The result of the action with the input confirmation message
    """
    if index not in await ctx.browser_context.get_selector_map():
        raise Exception(f'Element index {index} does not exist - retry or use alternative actions')

    element_node = await ctx.browser_context.get_dom_element_by_index(index)
    await ctx.browser_context._input_text_element_node(element_node, text)

    return ActionResult(action_result_msg=f'Input {text} into index {index}', 
                        success=True)


async def click_element(ctx: RunContextWrapper[MyAgentContext], index: int) -> ActionResult:
    """Click on an element identified by its index in the current page.
    
    Args:
        index: int - The index of the element to click.
        
    Returns:
        ActionResult - The result of the action with the click confirmation message or download path
    """
    session = await ctx.browser_context.get_session()

    if index not in await ctx.browser_context.get_selector_map():
        raise Exception(f'Element with index {index} does not exist - retry or use alternative actions')

    element_node = await ctx.browser_context.get_dom_element_by_index(index)
    initial_pages = len(session.context.pages)

    # if element has file uploader then dont click
    if await ctx.browser_context.is_file_uploader(element_node):
        return ActionResult(action_result_msg="Index {index} - has an element which opens file upload dialog. " +
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
        
        return ActionResult(action_result_msg=msg,
                            success=True)
    except Exception as e:
        return ActionResult(action_result_msg=f"Element not clickable with index {index} - most likely the page changed. Exception\n: {str(e)}",
                            success=False)


async def open_tab(ctx: RunContextWrapper[MyAgentContext], url: str) -> ActionResult:
    """Open a new tab with the specified URL.
    
    Args:
        url: str - The URL to open in the new tab.
        
    Returns:
        ActionResult - The result of the action with the tab opening confirmation message
    """
    await ctx.browser_context.create_new_tab(url)

    return ActionResult(action_result_msg=f'Opened new tab with {url}', 
                        success=True)


async def switch_tab(ctx: RunContextWrapper[MyAgentContext], page_id: int) -> ActionResult:
    """Switch to a specific tab by its ID.
    
    Args:
        page_id: int - The ID of the tab to switch to.
        
    Returns:
        ActionResult - The result of the action with the tab switching confirmation message
    """
    await ctx.browser_context.switch_to_tab(page_id)
    # Wait for tab to be ready
    page = await ctx.browser_context.get_current_page()
    await page.wait_for_load_state()
    
    return ActionResult(action_result_msg=f'Switched to tab {page_id}',
                        success=True)


async def scroll_down(ctx: RunContextWrapper[MyAgentContext], amount: int) -> ActionResult:
    """Scroll down the page by a specified amount of pixels.
    
    Args:
        amount: int - The number of pixels to scroll down. If 0, scrolls down one page height.
        
    Returns:
        ActionResult - The result of the action with the scroll confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    if amount != 0:
        await page.evaluate(f'window.scrollBy(0, {amount});')
    else:
        await page.evaluate('window.scrollBy(0, window.innerHeight);')

    amount_str = f'{amount} pixels' if amount != 0 else 'one page'
    return ActionResult(action_result_msg=f'Scrolled down the page by {amount_str}', 
                        success=True)


async def scroll_up(ctx: RunContextWrapper[MyAgentContext], amount: int) -> ActionResult:
    """Scroll up the page by a specified amount of pixels.
    
    Args:
        amount: int - The number of pixels to scroll up. If 0, scrolls up one page height.
        
    Returns:
        ActionResult - The result of the action with the scroll confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    if amount != 0:
        await page.evaluate(f'window.scrollBy(0, -{amount});')
    else:
        await page.evaluate('window.scrollBy(0, -window.innerHeight);')

    amount_str = f'{amount} pixels' if amount != 0 else 'one page'
    return ActionResult(action_result_msg=f'Scrolled up the page by {amount_str}', 
                        success=True)


async def send_keys(ctx: RunContextWrapper[MyAgentContext], keys: str) -> ActionResult:
    """Send strings of special keys like Escape, Backspace, Insert, PageDown, Delete, Enter, or shortcuts like 'Control+o', 'Control+Shift+T'.
    
    Args:
        keys: str - The keys to send. Can be special keys or keyboard shortcuts.
        
    Returns:
        ActionResult - The result of the action with the key press confirmation message
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
    
    return ActionResult(action_result_msg=f'Sent keys: {keys}',
                        success=True)


async def scroll_to_text(ctx: RunContextWrapper[MyAgentContext], text: str) -> ActionResult:
    """Scroll to a specific text on the page.
    
    Args:
        text: str - The text to scroll to.
        
    Returns:
        ActionResult - The result of the action with the scroll confirmation message
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
                    return ActionResult(action_result_msg=f'Scrolled to text: {text}',
                                        success=True)
            except Exception as e:
                logger.error(f'Locator attempt failed: {str(e)}')
                continue

        return ActionResult(action_result_msg=f"Text '{text}' not found or not visible on page",
                            success=False)

    except Exception as e:
        return ActionResult(action_result_msg=f"Failed to scroll to text '{text}': {str(e)}",
                            success=False)


async def get_dropdown_options(ctx: RunContextWrapper[MyAgentContext], index: int) -> ActionResult:
    """Get all options from a native dropdown.
    
    Args:
        index: int - The index of the dropdown element.
        
    Returns:
        ActionResult - The result of the action with the dropdown options
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
            return ActionResult(action_result_msg=msg,
                                success=True)
        else:
            msg = 'No options found in any frame for dropdown'
            return ActionResult(action_result_msg=msg,
                                success=False)

    except Exception as e:
        return ActionResult(action_result_msg=f'Error getting options: {str(e)}',
                            success=False)


async def select_dropdown_option(ctx: RunContextWrapper[MyAgentContext], index: int, text: str) -> ActionResult:
    """Select the option 'text' in the dropdown interactive element identified as 'index'.
    
    Args:
        index: int - The index of the dropdown element.
        text: str - The text of the option to select.
        
    Returns:
        ActionResult - The result of the action with the selection confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    selector_map = await ctx.browser_context.get_selector_map()
    dom_element = selector_map[index]

    # Validate that we're working with a select element
    if dom_element.tag_name != 'select':
        return ActionResult(action_result_msg=f'Cannot select option: Element with index {index} is a {dom_element.tag_name}, not a select',
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

                    return ActionResult(action_result_msg=f'Selected option {text} with value {selected_option_values}',
                                        success=True)

            except Exception as frame_e:
                logger.error(f'Frame {frame_index} attempt failed: {str(frame_e)}')
                logger.error(f'Frame type: {type(frame)}')
                logger.error(f'Frame URL: {frame.url}')

            frame_index += 1

        return ActionResult(action_result_msg=f"Could not select option '{text}' in any frame",
                            success=False)

    except Exception as e:
        return ActionResult(action_result_msg=f'Selection failed: {str(e)}',
                            success=False)


class MyNavigatorAgentTools():
    def __init__(self, ctx: MyAgentContext):
        self.ctx = ctx

        self.tools: List[Callable[[RunContextWrapper[MyAgentContext], Any], Awaitable[ActionResult]]] = [
            done,
            search_google,
            go_back,
            go_to_url,
            input_text,
            click_element,
            open_tab,
            switch_tab,
            scroll_down,
            scroll_up,
            send_keys,
            scroll_to_text,
            get_dropdown_options,
            select_dropdown_option,
        ]
        
    
    def get_tools(self):
        return self.tools
    

    @cached_property
    def tools_schema(self) -> list[dict]:		
        tools_schema = []
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
            return ActionResult(action_result_msg=f"Tool '{tool_name}' not found",
                                success=False)
        
        tool_args = json.loads(function_tool_call.arguments)

        return await tool(self.ctx, **tool_args)
        

async def wna_navigate_and_find(ctx: RunContextWrapper[MyAgentContext], navigation_goal: str) -> ActionResult:
    """Invoke the Web Navigation Agent (WNA) to autonomously browse the web until the navigation goal is satisfied.

    Args:
        navigation_goal: str - A concise, natural-language description of what the navigator should accomplish.
            It should focus on *where* to end up or *what* to locate, rather than prescribing individual clicks.
            Examples:
                - "Open https://news.ycombinator.com and scroll to the first post that mentions GPT".
                - "Go to finance.yahoo.com and bring me to the detailed quote page for NVDA.".
    """
    from my_navigator_agent import MyNavigatorAgent  # local import to avoid circular dependency
    navigator = MyNavigatorAgent(ctx=ctx, navigation_goal=navigation_goal)
    nav_result: ActionResult = await navigator.run()
    return ActionResult(action_result_msg=nav_result.action_result_msg, success=nav_result.success)


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
    try:
        agent = MyContentExtractAgent(
            ctx=ctx,
            extraction_goal=extraction_goal,
            row_schema=row_schema,            
        )

        rows, csv_path = await agent.run()

        result_payload = {
            "status": "success",
            "status_message": f"Successfully extracted and persisted {len(rows)} items to {csv_path}.",
            "persisted_count": len(rows),
        }
        return ActionResult(action_result_msg=json.dumps(result_payload), success=True)
    
    except Exception as e:
        result_payload = {
            "status": "failure",
            "status_message": str(e),
            "persisted_count": 0,
        }
        return ActionResult(action_result_msg=json.dumps(result_payload), success=False)


class MyBrainAgentTools:
    """Tools exposed to the Brain Agent (BA).

    These tools allow the BA to orchestrate sub-agents (WNA, CEA) and report completion.
    """
    def __init__(self, ctx: MyAgentContext):
        self.ctx = ctx
        self.tools: List[Callable[[RunContextWrapper[MyAgentContext], Any], Awaitable[ActionResult]]] = [
            done,
            wna_navigate_and_find,
            cea_extract_content,
        ]

    def get_tools(self):
        return self.tools

    @cached_property
    def tools_schema(self) -> list[dict]:
        tools_schema = []
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
            return ActionResult(action_result_msg=f"Tool '{tool_name}' not found", success=False)

        tool_args = json.loads(function_tool_call.arguments)
        return await tool(self.ctx, **tool_args)
        
