"""Strict Langflow v1 flow client. Langflow never owns controller state."""

import json
import re
import zlib
from collections.abc import Callable
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from controller.app.config import Settings


MAX_RESPONSE_BYTES = 2_000_000
FLOW_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ResultType = TypeVar("ResultType", bound=BaseModel)
Transport = Callable[[Request, float], bytes]


class LangflowUnavailable(Exception):
    pass


class LangflowInvalidOutput(Exception):
    pass


def _http_post(request: Request, timeout: float) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            encoding = response.headers.get("Content-Encoding", "").lower()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise LangflowUnavailable("Langflow request failed") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise LangflowInvalidOutput("Langflow response too large")
    if encoding == "gzip":
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decompressor.decompress(body, MAX_RESPONSE_BYTES + 1)
            if not decompressor.eof or decompressor.unused_data:
                raise LangflowInvalidOutput("Langflow gzip response is incomplete")
        except zlib.error as exc:
            raise LangflowInvalidOutput("Langflow gzip response is invalid") from exc
    elif encoding not in {"", "identity"}:
        raise LangflowInvalidOutput("Langflow response encoding is unsupported")
    return body


class LangflowClient:
    def __init__(
        self, base_url: str, api_key: str, *, timeout: float = 120.0,
        transport: Transport = _http_post,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Langflow URL must be local HTTP")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Langflow URL must contain only a local host and port")
        if not api_key:
            raise ValueError("Langflow API key is required")
        if timeout <= 0:
            raise ValueError("Langflow timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def run_json_flow(
        self, flow_id: str, input_data: dict, result_type: type[ResultType], *, session_id: str,
    ) -> ResultType:
        if not FLOW_ID_PATTERN.fullmatch(flow_id):
            raise ValueError("invalid Langflow flow ID")
        request = Request(
            f"{self.base_url}/api/v1/run/{flow_id}",
            data=json.dumps({
                "input_value": json.dumps(input_data, sort_keys=True, separators=(",", ":")),
                "input_type": "chat", "output_type": "chat", "session_id": session_id,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": self.api_key},
            method="POST",
        )
        body = self.transport(request, self.timeout)
        if len(body) > MAX_RESPONSE_BYTES:
            raise LangflowInvalidOutput("Langflow response too large")
        try:
            envelope = json.loads(body)
            outer = envelope["outputs"]
            if not isinstance(outer, list) or len(outer) != 1:
                raise ValueError("expected exactly one flow output")
            inner = outer[0]["outputs"]
            if not isinstance(inner, list) or len(inner) != 1:
                raise ValueError("expected exactly one component output")
            text = inner[0]["results"]["message"]["text"]
            if not isinstance(text, str):
                raise ValueError("message text is missing")
            payload = json.loads(text)
            return result_type.model_validate(payload)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise LangflowInvalidOutput("Langflow returned invalid structured output") from exc


def client_from_settings(settings: Settings) -> LangflowClient:
    return LangflowClient(settings.langflow_url, settings.langflow_api_key)
