import json
import os
import time
from pathlib import Path
from urllib import error, request


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
        self.timeout_seconds = int(timeout_seconds or get_env("LLM_DEFAULT_TIMEOUT_SECONDS", "180"))
        self.max_retries = int(max_retries or get_env("LLM_MAX_RETRIES", "2"))

    @property
    def enabled(self):
        return bool(self.api_base_url and self.api_key)

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

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                content = self._post_chat_completions(payload)
                return parse_json_content(content)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep([1, 3, 8][min(attempt, 2)])
        raise RuntimeError(f"LLM 调用失败：{last_error}") from last_error

    def _post_chat_completions(self, payload):
        if self.api_base_url.endswith("/chat/completions"):
            url = self.api_base_url
        else:
            url = f"{self.api_base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

        result = json.loads(body)
        return result["choices"][0]["message"]["content"]


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
