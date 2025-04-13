import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Generic, List, Optional, Type, TypeVar
from functools import cached_property
from browser_use import Browser
from pydantic import BaseModel

from openai import OpenAI
from openai.types.responses import ResponseFunctionToolCall
from agents.function_schema import function_schema
from agents import Agent, FunctionTool, RunContextWrapper, function_tool
from agents.run import Runner
from browser_use.browser.context import BrowserContext

logger = logging.getLogger(__name__)


class ActionResult(BaseModel):
    is_done: Optional[bool] = False
    success: Optional[bool] = None
    extracted_content: Optional[str] = None
    error: Optional[str] = None
    include_in_memory: bool = False         # whether to include in past messages as context or not


@dataclass
class MyToolContext:
    browser_context: BrowserContext
    openai_client: OpenAI       # We need this because of "extract_content". But, wouldn't it be better if we had "Agents as tools" instead?


async def done(ctx: RunContextWrapper[MyToolContext], success: bool, message_to_user: str) -> ActionResult:
    """The agent has finished the task with success or failure, for whatever the reason.
    
    Args:
        success: bool - Whether the task is finished successfully or not.
        message_to_user: str - A message to return to the user in either case of success or failure.
        
    Returns:
        ActionResult - The result of the action
    """	
    return ActionResult(is_done=True, success=success, extracted_content=message_to_user)


async def search_google(ctx: RunContextWrapper[MyToolContext], query: str) -> ActionResult:
    """Search the query in Google in the current tab.
    
    Args:
        query: str - The search query to use. Should be concrete and not vague or super long, like how humans search in Google. Focus on the single most important items.
        
    Returns:
        ActionResult - The result of the action with the search confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    await page.goto(f'https://www.google.com/search?q={query}&udm=14')
    await page.wait_for_load_state()
    msg = f'🔍  Searched for "{query}" in Google'	
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def wait(ctx: RunContextWrapper[MyToolContext], seconds: int) -> ActionResult:
    """Wait for a specified number of seconds.
    
    Args:
        seconds: int - The number of seconds to wait.
        
    Returns:
        ActionResult - The result of the action with the wait confirmation message
    """
    msg = f'🕒  Waiting for {seconds} seconds'
    logger.info(msg)
    await asyncio.sleep(seconds)
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def go_back(ctx: RunContextWrapper[MyToolContext]) -> ActionResult:
    """Navigate back to the previous page in the browser history of the current tab.
            
    Returns:
        ActionResult - The result of the action with the navigation confirmation message
    """
    await ctx.browser_context.go_back()
    msg = '🔙  Navigated back'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def go_to_url(ctx: RunContextWrapper[MyToolContext], url: str) -> ActionResult:
    """Navigate to a specific URL in the current tab.
    
    Args:
        url: str - The URL to navigate to.
                
    Returns:
        ActionResult - The result of the action with the navigation confirmation message
    """
    page = await ctx.browser_context.get_current_page()
    await page.goto(url)
    await page.wait_for_load_state()
    msg = f'🔗  Navigated to {url}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def input_text(ctx: RunContextWrapper[MyToolContext], index: int, text: str) -> ActionResult:
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
    msg = f'⌨️  Input {text} into index {index}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def click_element(ctx: RunContextWrapper[MyToolContext], index: int) -> ActionResult:
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
        msg = f'Index {index} - has an element which opens file upload dialog. To upload files please use a specific function to upload files'
        return ActionResult(extracted_content=msg, include_in_memory=True)

    try:
        download_path = await ctx.browser_context._click_element_node(element_node)
        if download_path:
            msg = f'💾  Downloaded file to {download_path}'
        else:
            msg = f'🖱️  Clicked button with index {index}: {element_node.get_all_text_till_next_clickable_element(max_depth=2)}'

        if len(session.context.pages) > initial_pages:
            new_tab_msg = 'New tab opened - switching to it'
            msg += f' - {new_tab_msg}'
            await ctx.browser_context.switch_to_tab(-1)
        return ActionResult(extracted_content=msg, include_in_memory=True)
    except Exception as e:
        error_msg = f"Element not clickable with index {index} - most likely the page changed. Exception\n: {str(e)}"
        return ActionResult(error=error_msg)


async def open_tab(ctx: RunContextWrapper[MyToolContext], url: str) -> ActionResult:
    """Open a new tab with the specified URL.
    
    Args:
        url: str - The URL to open in the new tab.
        
    Returns:
        ActionResult - The result of the action with the tab opening confirmation message
    """
    await ctx.browser_context.create_new_tab(url)
    msg = f'🔗  Opened new tab with {url}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def switch_tab(ctx: RunContextWrapper[MyToolContext], page_id: int) -> ActionResult:
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
    msg = f'🔄  Switched to tab {page_id}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def extract_content(ctx: RunContextWrapper[MyToolContext], goal: str) -> ActionResult:
    """Extract content from the current page based on a specific goal.
    
    Args:
        goal: str - The goal or purpose of the content extraction.
        
    Returns:
        ActionResult - The result of the action with the extracted content
    """
    page = await ctx.browser_context.get_current_page()
    import markdownify
    content = markdownify.markdownify(await page.content())
    prompt = 'Your task is to extract the content of the page. You will be given a page and a goal and you should extract all relevant information around this goal from the page. If the goal is vague, summarize the page. Respond in json format. Extraction goal: {goal}, Page: {page}'
    
    try:
        msg = f'📄  Extracted from page\n: TODO\n'
        return ActionResult(extracted_content=msg, include_in_memory=True)
    except Exception as e:
        msg = f'📄  Error extracting from page, Exception:\n{str(e)}'
        return ActionResult(error=msg)


async def scroll_down(ctx: RunContextWrapper[MyToolContext], amount: int) -> ActionResult:
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
    msg = f'🔍  Scrolled down the page by {amount_str}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def scroll_up(ctx: RunContextWrapper[MyToolContext], amount: int) -> ActionResult:
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
    msg = f'🔍  Scrolled up the page by {amount_str}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def send_keys(ctx: RunContextWrapper[MyToolContext], keys: str) -> ActionResult:
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
            # loop over the keys and try to send each one
            for key in keys:
                try:
                    await page.keyboard.press(key)
                except Exception as e:
                    logger.debug(f'Error sending key {key}: {str(e)}')
                    raise e
        else:
            raise e
    msg = f'⌨️  Sent keys: {keys}'
    return ActionResult(extracted_content=msg, include_in_memory=True)


async def scroll_to_text(ctx: RunContextWrapper[MyToolContext], text: str) -> ActionResult:
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
                    msg = f'🔍  Scrolled to text: {text}'
                    return ActionResult(extracted_content=msg, include_in_memory=True)
            except Exception as e:
                logger.debug(f'Locator attempt failed: {str(e)}')
                continue

        msg = f"Text '{text}' not found or not visible on page"
        return ActionResult(extracted_content=msg, include_in_memory=True)

    except Exception as e:
        msg = f"Failed to scroll to text '{text}': {str(e)}"
        return ActionResult(error=msg, include_in_memory=True)


async def get_dropdown_options(ctx: RunContextWrapper[MyToolContext], index: int) -> ActionResult:
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
                logger.debug(f'Frame {frame_index} evaluation failed: {str(frame_e)}')

            frame_index += 1

        if all_options:
            msg = '\n'.join(all_options)
            msg += '\nUse the exact text string in select_dropdown_option'
            logger.info(msg)
            return ActionResult(extracted_content=msg, include_in_memory=True)
        else:
            msg = 'No options found in any frame for dropdown'
            logger.info(msg)
            return ActionResult(extracted_content=msg, include_in_memory=True)

    except Exception as e:
        logger.error(f'Failed to get dropdown options: {str(e)}')
        msg = f'Error getting options: {str(e)}'
        logger.info(msg)
        return ActionResult(extracted_content=msg, include_in_memory=True)


async def select_dropdown_option(ctx: RunContextWrapper[MyToolContext], index: int, text: str) -> ActionResult:
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
        logger.error(f'Element is not a select! Tag: {dom_element.tag_name}, Attributes: {dom_element.attributes}')
        msg = f'Cannot select option: Element with index {index} is a {dom_element.tag_name}, not a select'
        return ActionResult(extracted_content=msg, include_in_memory=True)

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

                    msg = f'selected option {text} with value {selected_option_values}'
                    logger.info(msg + f' in frame {frame_index}')

                    return ActionResult(extracted_content=msg, include_in_memory=True)

            except Exception as frame_e:
                logger.error(f'Frame {frame_index} attempt failed: {str(frame_e)}')
                logger.error(f'Frame type: {type(frame)}')
                logger.error(f'Frame URL: {frame.url}')

            frame_index += 1

        msg = f"Could not select option '{text}' in any frame"
        logger.info(msg)
        return ActionResult(extracted_content=msg, include_in_memory=True)

    except Exception as e:
        msg = f'Selection failed: {str(e)}'
        logger.error(msg)
        return ActionResult(error=msg, include_in_memory=True)


class MyAgentTools():
    def __init__(self, browser_context: BrowserContext, openai_client: OpenAI):
        self.tools: List[Callable[[RunContextWrapper[MyToolContext], Any], Awaitable[ActionResult]]] = [
            done,
            search_google,
            wait,   
            go_back,
            go_to_url,
            input_text,
            click_element,
            open_tab,
            switch_tab,
            extract_content,
            scroll_down,
            scroll_up,
            send_keys,
            scroll_to_text,
            get_dropdown_options,
            select_dropdown_option,
        ]
        self.ctx = MyToolContext(browser_context=browser_context, openai_client=openai_client)
                            
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
        # Find the tool by name
        tool_name = function_tool_call.name
        tool = next((t for t in self.tools if t.__name__ == tool_name), None)
        if not tool:
            return ActionResult(error=f"Tool '{tool_name}' not found")
        
        tool_args = json.loads(function_tool_call.arguments)

        return await tool(self.ctx, **tool_args)
        
