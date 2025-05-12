- Revise error messages in my_navigator_agent_tools. The agent has to be able to recover from them.
    - An unrecoverable error is an exception.
    - Every other error has to have a nice message that the agent needs to see and act in consequence.

- Test other markdown library. Microsoft and others have recently released possible better ones than markdownify.

- Is Step a function tool in itself that the agent can call?

- Check out all these options: {'error': {'message': "Invalid value: 'input_image'. Supported values are: 'computer_call', 'computer_call_output', 'file_search_call', 'function_call', 'function_call_output', 'item_reference', 'message', 'reasoning', and 'web_search_call'.", 'type': 'invalid_request_error', 'param': 'input[2]', 'code': 'invalid_value'}}