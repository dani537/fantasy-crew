import re
import json


def extract_json_from_llm(text: str) -> dict:
    """
    Extracts the first JSON object from an LLM response.

    Handles responses wrapped in ```json ... ``` blocks and strips
    full-line `//` comments (which LLMs sometimes add) WITHOUT touching
    URLs such as "https://..." embedded in string values.
    """
    if not text:
        return {}

    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    json_str = match.group(1) if match else text

    # Remove only comments that span a whole line (optionally indented).
    # Inline stripping of "//.*" would corrupt URLs inside string values.
    json_str = re.sub(r'^\s*//.*$', '', json_str, flags=re.MULTILINE)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback: try to locate the outermost {...} block
        brace_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError as e:
                print(f"⚠️ Error parsing JSON: {e}")
        else:
            print("⚠️ No JSON object found in LLM output.")
        return {"error": "Failed to parse JSON", "raw_output": text}
