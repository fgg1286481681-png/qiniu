import json
import os
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlparse

from cancellation import CancelledError, check_cancelled, get_current_token


BASE_DIR = Path(__file__).resolve().parents[1]


def load_env_file(path=None):
    env_path = Path(path) if path else BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env(name, default=None):
    load_env_file()
    return os.environ.get(name, default)


class LLMClient:
    """OpenAI-compatible chat completions client using only Python stdlib."""

    def __init__(self, api_base_url=None, api_key=None, timeout_seconds=None, max_retries=None):
        self.api_base_url = (api_base_url or get_env("LLM_API_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or get_env("LLM_API_KEY", "")
        self.timeout_seconds = int(
            get_env("LLM_DEFAULT_TIMEOUT_SECONDS", "180")
            if timeout_seconds is None
            else timeout_seconds
        )
        self.max_retries = int(
            get_env("LLM_MAX_RETRIES", "2")
            if max_retries is None
            else max_retries
        )

    @property
    def enabled(self):
        return bool(self.api_base_url and self.api_key)

    def probe(self, *, model):
        """Check authentication and model reachability without parsing model text."""
        if not self.enabled:
            raise RuntimeError("LLM_API_BASE_URL 或 LLM_API_KEY 未配置")
        if not model:
            raise RuntimeError("模型名称未配置")

        response = self._post_chat_completions(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "回复 OK，用于服务连通性检测。",
                    }
                ],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": 128,
                "stream": False,
            }
        )
        content = response.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("模型已响应，但返回内容为空")
        return {
            "content": content.strip(),
            "usage": response.get("usage"),
            "model": response.get("model") or model,
        }

    def chat_json(
        self,
        *,
        model,
        system_prompt,
        user_prompt,
        temperature=0.1,
        top_p=0.9,
        max_tokens=8000,
        presence_penalty=None,
        frequency_penalty=None,
        response_format_json=False,
    ):
        if not self.enabled:
            raise RuntimeError("LLM_API_BASE_URL 或 LLM_API_KEY 未配置")
        if not model:
            raise RuntimeError("模型名称未配置")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                check_cancelled()
                response = self._post_chat_completions(payload)
                check_cancelled()
                return {
                    "data": parse_json_content(response["content"]),
                    "usage": response.get("usage"),
                    "attempts": attempt + 1,
                    "model": response.get("model") or model,
                }
            except CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                sleep_with_cancel([1, 3, 8][min(attempt, 2)])
        raise RuntimeError(f"LLM 调用失败：{last_error}") from last_error

    def _post_chat_completions(self, payload):
        if self.api_base_url.endswith("/chat/completions"):
            url = self.api_base_url
        else:
            url = f"{self.api_base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token = get_current_token()
        if token:
            token.check()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(f"Invalid LLM_API_BASE_URL: {self.api_base_url}")
        connection = create_connection(parsed, self.timeout_seconds)
        close_connection = connection.close
        if token:
            token.register_closer(close_connection)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            request_bytes = build_http_request(
                parsed,
                path,
                data,
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            connection.sendall(request_bytes)
            deadline = time.monotonic() + self.timeout_seconds
            status, body = read_http_response(connection, token, deadline)
            if status >= 400:
                raise RuntimeError(f"HTTP {status}: {body}")
        except CancelledError:
            raise
        except Exception as exc:
            if token and token.cancelled:
                raise CancelledError("用户取消了生成任务") from exc
            raise
        finally:
            if token:
                token.unregister_closer(close_connection)
            connection.close()

        result = json.loads(body)
        return {
            "content": result["choices"][0]["message"]["content"],
            "usage": result.get("usage"),
            "model": result.get("model"),
        }


def sleep_with_cancel(seconds):
    end_time = time.monotonic() + seconds
    while time.monotonic() < end_time:
        check_cancelled()
        time.sleep(min(0.1, end_time - time.monotonic()))
    check_cancelled()


def create_connection(parsed, timeout_seconds):
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    raw_socket = socket.create_connection((parsed.hostname, port), timeout=timeout_seconds)
    raw_socket.settimeout(0.2)
    if parsed.scheme == "https":
        context = ssl.create_default_context()
        wrapped_socket = context.wrap_socket(raw_socket, server_hostname=parsed.hostname)
        wrapped_socket.settimeout(0.2)
        return wrapped_socket
    return raw_socket


def build_http_request(parsed, path, body, headers):
    host = parsed.hostname or parsed.netloc
    if parsed.port:
        host = f"{host}:{parsed.port}"
    request_headers = {
        "Host": host,
        "Accept": "application/json",
        "Connection": "close",
        "Content-Length": str(len(body)),
        **headers,
    }
    header_text = "".join(f"{key}: {value}\r\n" for key, value in request_headers.items())
    return f"POST {path} HTTP/1.1\r\n{header_text}\r\n".encode("utf-8") + body


def read_http_response(connection, token=None, deadline=None):
    raw = bytearray()
    header_end = -1
    while True:
        if token:
            token.check()
        check_deadline(deadline)
        try:
            chunk = connection.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        raw.extend(chunk)
        header_end = raw.find(b"\r\n\r\n")
        if header_end >= 0:
            break
    if header_end < 0:
        raise RuntimeError("LLM response missing HTTP headers")

    header_bytes = bytes(raw[:header_end])
    body = bytearray(raw[header_end + 4 :])
    header_lines = header_bytes.decode("iso-8859-1").splitlines()
    status = int(header_lines[0].split()[1])
    headers = {}
    for line in header_lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = read_chunked_body(connection, body, token, deadline)
    elif "content-length" in headers:
        expected_length = int(headers["content-length"])
        while len(body) < expected_length:
            if token:
                token.check()
            check_deadline(deadline)
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            body.extend(chunk)
        body = body[:expected_length]
    else:
        while True:
            if token:
                token.check()
            check_deadline(deadline)
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            body.extend(chunk)
    return status, bytes(body).decode("utf-8")


def read_chunked_body(connection, initial_body, token=None, deadline=None):
    buffer = bytearray(initial_body)
    decoded = bytearray()
    while True:
        line_end = find_line_end(buffer)
        while line_end < 0:
            buffer.extend(recv_with_cancel(connection, token, deadline))
            line_end = find_line_end(buffer)
        size_line = bytes(buffer[:line_end]).decode("ascii", errors="replace")
        del buffer[: line_end + 2]
        chunk_size = int(size_line.split(";", 1)[0], 16)
        if chunk_size == 0:
            return decoded
        while len(buffer) < chunk_size + 2:
            buffer.extend(recv_with_cancel(connection, token, deadline))
        decoded.extend(buffer[:chunk_size])
        del buffer[: chunk_size + 2]


def recv_with_cancel(connection, token=None, deadline=None):
    while True:
        if token:
            token.check()
        check_deadline(deadline)
        try:
            chunk = connection.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            raise RuntimeError("LLM response ended unexpectedly")
        return chunk


def check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("LLM request timed out")


def find_line_end(buffer):
    return bytes(buffer).find(b"\r\n")


def parse_json_content(content):
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)
