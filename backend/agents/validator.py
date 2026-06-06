import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from llm_client import LLMClient, get_env
from trace_utils import TraceTimer


BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = BASE_DIR / "schema.json"


class ValidatorAgent:
    """Local schema validation plus optional AI quality review."""

    name = "Validator Agent"

    def __init__(self, provider=None):
        self.provider = provider or build_validator_provider()

    def run(self, script):
        timer = TraceTimer(
            self.name,
            "validator",
            getattr(self.provider, "model", None),
        )
        validation = validate_script(script)
        source = "rule"
        attempts = 0
        usage = None
        fallback_reason = None

        if validation["valid"] and self.provider:
            try:
                provider_result = self.provider.review(script)
                ai_review = provider_result["review"]
                attempts = provider_result["attempts"]
                usage = provider_result["usage"]
                validation["ai_review"] = ai_review
                source = "llm"
            except Exception as exc:
                fallback_reason = str(exc)
                validation["ai_review"] = {
                    "score": None,
                    "issues": [],
                    "requires_rewrite": False,
                    "error": fallback_reason,
                }

        ai_review = validation.get("ai_review") or {}
        requires_rewrite = bool(ai_review.get("requires_rewrite"))
        status = "degraded" if fallback_reason else "success"
        if not validation["valid"] or requires_rewrite:
            status = "failed"

        if not validation["valid"]:
            summary = f"发现 {len(validation['errors'])} 个 Schema 或引用问题"
        elif requires_rewrite:
            summary = f"结构校验通过，AI 质量评分 {ai_review.get('score')}，建议局部重写"
        else:
            score_text = f"，AI 质量评分 {ai_review.get('score')}" if ai_review.get("score") is not None else ""
            summary = f"Schema 和引用校验通过{score_text}，来源：{source}"

        return {
            "validation": validation,
            "trace": timer.finish(
                status=status,
                source=source,
                summary=summary,
                attempts=attempts,
                fallback_reason=fallback_reason,
                usage=usage,
            ),
        }


class ValidatorLLMProvider:
    def __init__(self, client=None, model=None):
        self.client = client or LLMClient()
        self.model = model or get_env("VALIDATOR_MODEL", "")
        self.temperature = float(get_env("VALIDATOR_TEMPERATURE", "0.1"))
        self.top_p = float(get_env("VALIDATOR_TOP_P", "1.0"))
        self.max_tokens = int(get_env("VALIDATOR_MAX_TOKENS", "3000"))

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def review(self, script):
        if not self.enabled:
            raise RuntimeError("Validator LLM 未配置")

        response = self.client.chat_json(
            model=self.model,
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            user_prompt=json.dumps(script, ensure_ascii=False),
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
        )
        return {
            "review": normalize_ai_review(response["data"]),
            "usage": response["usage"],
            "attempts": response["attempts"],
        }


def build_validator_provider():
    provider = ValidatorLLMProvider()
    return provider if provider.enabled else None


VALIDATOR_SYSTEM_PROMPT = """你是 Novel2Script 的 Validator Agent。
输入是一份已经通过 JSON Schema 的中文短剧剧本。
你只负责质量评审，不要重写全文。检查：
1. 场景是否覆盖事件主线；
2. 人物行为和对白是否一致；
3. 是否存在明显幻觉、逻辑断裂；
4. 场景是否有目的、冲突和情绪转折；
5. 对白是否过度模板化。

只输出严格 JSON：
{
  "score": 0到100的整数,
  "issues": [
    {
      "type": "continuity|character|fidelity|dialogue|scene",
      "severity": "low|medium|high|critical",
      "scene_id": "scene_001或空字符串",
      "message": "问题描述",
      "suggested_fix": "修复建议"
    }
  ],
  "requires_rewrite": true或false,
  "rewrite_scope": ["scene_001"]
}
只有 high 或 critical 问题才应将 requires_rewrite 设为 true。
"""


def normalize_ai_review(result):
    issues = []
    for item in result.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "type": str(item.get("type") or "scene"),
                "severity": str(item.get("severity") or "low"),
                "scene_id": str(item.get("scene_id") or ""),
                "message": str(item.get("message") or ""),
                "suggested_fix": str(item.get("suggested_fix") or ""),
            }
        )
    score = result.get("score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = None

    return {
        "score": score,
        "issues": issues,
        "requires_rewrite": bool(result.get("requires_rewrite")),
        "rewrite_scope": [str(item) for item in result.get("rewrite_scope") or []],
    }


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


SCRIPT_SCHEMA = load_schema()
SCHEMA_VALIDATOR = Draft202012Validator(SCRIPT_SCHEMA)


def parse_yaml_script(yaml_text):
    try:
        script = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败：{exc}") from exc

    if not isinstance(script, dict):
        raise ValueError("YAML 顶层必须是对象")
    return script


def path_to_string(parts):
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path = f"{path}.{part}" if path else str(part)
    return path or "$"


def validation_error_to_tip(error):
    if error.validator == "required":
        missing = error.message.split("'")[1] if "'" in error.message else ", ".join(error.validator_value)
        return f"补充必填字段：{missing}"
    if error.validator == "type":
        return f"调整为 {error.validator_value} 类型"
    if error.validator == "enum":
        return f"改为允许值之一：{', '.join(map(str, error.validator_value))}"
    if error.validator == "minItems":
        return f"至少保留 {error.validator_value} 项"
    return "按 schema.json 调整该字段结构"


def schema_errors(script):
    errors = []
    for error in sorted(SCHEMA_VALIDATOR.iter_errors(script), key=lambda item: list(item.path)):
        errors.append(
            {
                "path": path_to_string(error.path),
                "message": error.message,
                "suggestion": validation_error_to_tip(error),
            }
        )
    return errors


def business_errors(script):
    errors = []
    character_ids = {item.get("id") for item in script.get("characters", []) if isinstance(item, dict)}
    location_ids = {item.get("id") for item in script.get("locations", []) if isinstance(item, dict)}
    chapter_ids = {item.get("id") for item in script.get("chapters", []) if isinstance(item, dict)}
    event_ids = {item.get("id") for item in script.get("events", []) if isinstance(item, dict)}

    for collection_name in ["chapters", "characters", "locations", "events", "scenes"]:
        ids = [
            item.get("id")
            for item in script.get(collection_name, [])
            if isinstance(item, dict)
        ]
        duplicates = sorted({item for item in ids if item and ids.count(item) > 1})
        for duplicate in duplicates:
            errors.append(
                {
                    "path": collection_name,
                    "message": f"存在重复 ID：{duplicate}",
                    "suggestion": "确保同类对象的 id 唯一。",
                }
            )

    for event_index, event in enumerate(script.get("events", [])):
        if event.get("source_chapter") not in chapter_ids:
            errors.append(
                {
                    "path": f"events[{event_index}].source_chapter",
                    "message": "引用了不存在的章节 ID",
                    "suggestion": "改为 chapters 中已有的 id。",
                }
            )
        for character_index, character_id in enumerate(event.get("characters", [])):
            if character_id not in character_ids:
                errors.append(
                    {
                        "path": f"events[{event_index}].characters[{character_index}]",
                        "message": "事件引用了不存在的人物 ID",
                        "suggestion": "改为 characters 中已有的 id。",
                    }
                )

    for scene_index, scene in enumerate(script.get("scenes", [])):
        if scene.get("heading", {}).get("location_id") not in location_ids:
            errors.append(
                {
                    "path": f"scenes[{scene_index}].heading.location_id",
                    "message": "引用了不存在的地点 ID",
                    "suggestion": "改为 locations 中已有的 id。",
                }
            )
        for chapter_index, chapter_id in enumerate(scene.get("source_chapters", [])):
            if chapter_id not in chapter_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].source_chapters[{chapter_index}]",
                        "message": "场景引用了不存在的章节 ID",
                        "suggestion": "改为 chapters 中已有的 id。",
                    }
                )
        for character_index, character_id in enumerate(scene.get("characters", [])):
            if character_id not in character_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].characters[{character_index}]",
                        "message": "场景引用了不存在的人物 ID",
                        "suggestion": "改为 characters 中已有的 id。",
                    }
                )
        for element_index, element in enumerate(scene.get("elements", [])):
            if element.get("type") == "dialogue" and element.get("character_id") not in character_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].elements[{element_index}].character_id",
                        "message": "对白引用了不存在的人物 ID",
                        "suggestion": "改为 characters 中已有的 id。",
                    }
                )
            if element.get("event_id") and element.get("event_id") not in event_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].elements[{element_index}].event_id",
                        "message": "剧本元素引用了不存在的事件 ID",
                        "suggestion": "改为 events 中已有的 id，或移除 event_id。",
                    }
                )
    return errors


def validate_script(script):
    errors = schema_errors(script)
    if not errors:
        errors.extend(business_errors(script))
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "script": script if len(errors) == 0 else None,
    }
