import json
import re
from typing import Dict, Any

def extract_json_from_response(response_text: str) -> Dict[str, Any]:
    """Extract JSON from text, trying fenced blocks first, then a balanced‐braces fallback."""
    # 1) Try all ```json``` or ``` fenced blocks
    fence_pattern = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.DOTALL)
    for match in fence_pattern.finditer(response_text):
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # clean trailing commas inside brackets/braces
            cleaned = re.sub(r',\s*(?=[\}\]])', '', candidate)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                continue

    # 2) Fallback: find the largest {...} block with balanced braces
    brace_stack = []
    start_idx = None
    best_json = ""
    for i, ch in enumerate(response_text):
        if ch == '{':
            brace_stack.append(i)
            if start_idx is None:
                start_idx = i
        elif ch == '}' and brace_stack:
            brace_stack.pop()
            if not brace_stack and start_idx is not None:
                candidate = response_text[start_idx:i+1]
                if len(candidate) > len(best_json):
                    best_json = candidate
                start_idx = None

    if best_json:
        # strip control chars
        best_json = re.sub(r'[\x00-\x1F\x7F]', '', best_json)
        try:
            return json.loads(best_json)
        except json.JSONDecodeError as e:
            # final cleaning: remove trailing commas and unescaped newlines
            cleaned = re.sub(r',\s*(?=[\}\]])', '', best_json)
            cleaned = cleaned.replace('\n', '\\n')
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    # 3) Give up
    return {
        "error": "Failed to parse JSON from model response",
        "raw_response": response_text[:500]
    }
