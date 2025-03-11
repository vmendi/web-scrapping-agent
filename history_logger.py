import base64
import json
import os

def print_message_with_header(header, message):
    print("\n")
    print("*" * 80)
    print(header)
    print("*" * 80)
    print(message)

def remove_screenshots(obj):
    if isinstance(obj, dict):
        # Create a new dict without "screenshot" keys
        return {k: remove_screenshots(v) for k, v in obj.items() if k != "screenshot"}
    elif isinstance(obj, list):
        # Process each item in the list
        return [remove_screenshots(item) for item in obj]
    else:
        # Return primitive values as is
        return obj
    
def save_history_to_disk(history, log_dir, prefix):
    with open(os.path.join(log_dir, f"{prefix}_history_complete.json"), "w") as f:
        json_history = history.model_dump_json(indent=2)
        json_history = json.loads(json_history)
        json_history = remove_screenshots(json_history)
        json.dump(json_history, f, indent=2)
    
    class CustomEncoder(json.JSONEncoder):
        def default(self, obj):
            try:
                return str(obj)
            except:
                return f"<Non-serializable object of type {type(obj).__name__}>"
    
    history_data = {
        "visited_urls": history.urls(),
        "executed_actions": history.action_names(),
        "extracted_content": history.extracted_content(),
        "errors": history.errors(),
        "model_actions": history.model_actions(),
        "final_result": history.final_result(),
    }
    
    with open(os.path.join(log_dir, f"{prefix}_history_components.json"), "w") as f:
        json.dump(history_data, f, indent=2, cls=CustomEncoder)

    screenshot_number = 0
    for screenshot in history.screenshots():
        screenshot_file = os.path.join(log_dir, f"{prefix}_screenshot_{screenshot_number}.png")
        with open(screenshot_file, "wb") as f:
            f.write(base64.b64decode(screenshot))
        screenshot_number += 1

def print_history_summary(history):
    print_message_with_header("VISITED URLS", history.urls())
    print_message_with_header("EXECUTED ACTIONS", history.action_names())
    print_message_with_header("EXTRACTED CONTENT", history.extracted_content())
    print_message_with_header("ERRORS", history.errors())
    print_message_with_header("MODEL ACTIONS WITH PARAMETERS", history.model_actions())
    print_message_with_header("FINAL RESULT", history.final_result()) 