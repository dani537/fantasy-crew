import re
import json


def extract_json_from_llm(text: str) -> dict:
    """
    Extracts a JSON object from an LLM response robustly.
    1. Tries code block extraction ```json ... ``` or ``` ... ```
    2. Strips single/full-line // comments.
    3. Uses json.loads and JSONDecoder().raw_decode() to locate any embedded JSON object.
    """
    if not text:
        return {}

    # 1. Try markdown codeblocks
    match = re.search(r'```(?:json)?\s*(.*?)\s*(?:```|$)', text, re.DOTALL)
    json_str = match.group(1).strip() if match else text.strip()

    # 2. Clean line comments
    json_str_cleaned = re.sub(r'^\s*//.*$', '', json_str, flags=re.MULTILINE)

    # 3. Direct parse try
    try:
        return json.loads(json_str_cleaned)
    except json.JSONDecodeError:
        pass

    # 4. Scanning fallback using raw_decode starting at every '{'
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start_idx = json_str.find('{', pos)
        if start_idx == -1:
            break
        try:
            obj, _ = decoder.raw_decode(json_str, start_idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        pos = start_idx + 1

    # 5. Global text scanning fallback if codeblock extraction failed or was incomplete
    if json_str != text:
        pos = 0
        while True:
            start_idx = text.find('{', pos)
            if start_idx == -1:
                break
            try:
                obj, _ = decoder.raw_decode(text, start_idx)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            pos = start_idx + 1

    print("⚠️ Error parsing JSON: No valid JSON object found in LLM response.")
    return {"error": "Failed to parse JSON", "raw_output": text}

