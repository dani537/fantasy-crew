import re
import json

def extract_json_from_llm(text: str) -> dict:
    if not text:
        return {}
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    json_str = match.group(1) if match else text
    try:
        # Pre-process to remove potential comments //
        json_str_clean = re.sub(r'//.*', '', json_str)
        return json.loads(json_str_clean)
    except json.JSONDecodeError as e:
        print(f"⚠️ Error parsing JSON: {e}")
        return {"error": "Failed to parse JSON", "raw_output": text}
