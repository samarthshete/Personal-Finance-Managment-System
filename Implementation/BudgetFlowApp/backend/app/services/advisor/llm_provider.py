"""
LLM provider abstraction. Production uses OpenAI; tests inject FakeLLM.
"""
import json
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.services.advisor.tool_registry import TOOL_DEFINITIONS


@runtime_checkable
class LLMProvider(Protocol):
    async def chat_completion(
        self, messages: List[Dict], tools: Optional[List[Dict]] = None,
    ) -> dict:
        """Return an OpenAI-compatible chat completion response dict."""
        ...


def openai_tool_schema() -> List[Dict]:
    return [
        {"type": "function", "function": defn}
        for defn in TOOL_DEFINITIONS.values()
    ]


class OpenAIProvider:
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        import httpx
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or "https://api.openai.com").rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    async def chat_completion(
        self, messages: List[Dict], tools: Optional[List[Dict]] = None,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        resp = await self._client.post(
            f"{self._base_url}/v1/chat/completions",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


class FakeLLM:
    """Deterministic LLM for tests. Returns canned tool calls or text."""

    def __init__(self, responses: Optional[List[Dict]] = None):
        self._responses = list(responses or [])
        self._call_idx = 0
        self.calls: List[Dict[str, Any]] = []

    def push(self, response: dict):
        self._responses.append(response)

    async def chat_completion(
        self, messages: List[Dict], tools: Optional[List[Dict]] = None,
    ) -> dict:
        self.calls.append({"messages": messages, "tools": tools})
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return resp
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "I don't have enough information to answer that.",
                },
                "finish_reason": "stop",
            }]
        }

    @staticmethod
    def make_tool_call_response(tool_name: str, args: dict) -> dict:
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"call_{tool_name}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(args),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        }

    @staticmethod
    def make_text_response(text: str) -> dict:
        return {
            "choices": [{
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }]
        }
