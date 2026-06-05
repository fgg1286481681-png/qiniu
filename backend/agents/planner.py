import re


class PlannerAgent:
    """规则版 Planner，后续可替换为 AI 信息抽取与场景规划 Provider。"""

    name = "Planner Agent"

    def __init__(self, provider=None):
        self.provider = provider

    def run(self, chapters):
        if len(chapters) < 3:
            raise ValueError("至少需要 3 个章节以上的小说文本")

        characters = infer_characters(chapters)
        locations = infer_locations(chapters)
        events = build_events(chapters, characters)
        return {
            "plan": {
                "chapters": chapters,
                "characters": characters,
                "locations": locations,
                "events": events,
            },
            "trace": {
                "agent": self.name,
                "status": "success",
                "summary": f"规划出 {len(characters)} 个人物、{len(locations)} 个地点、{len(events)} 个事件",
            },
        }


def split_sentences(text):
    return [item.strip() for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]


def infer_characters(chapters):
    candidates = {}
    ignored_prefixes = ("第一", "第二", "第三", "这个", "那个", "他们", "我们", "时候", "声音", "会议", "办公室")
    for chapter in chapters:
        for name in re.findall(r"[\u4e00-\u9fa5]{2,4}", chapter["text"]):
            if name.startswith(ignored_prefixes):
                continue
            candidates[name] = candidates.get(name, 0) + 1

    sorted_names = [name for name, _ in sorted(candidates.items(), key=lambda item: item[1], reverse=True)[:6]]
    if not sorted_names:
        sorted_names = ["主角", "对手", "旁观者"]

    roles = ["protagonist", "supporting", "antagonist", "supporting", "supporting", "supporting"]
    return [
        {
            "id": f"char_{index + 1:03d}",
            "name": name,
            "aliases": [],
            "role": roles[index] if index < len(roles) else "supporting",
            "description": f"{name} 是从原文中识别出的关键人物，参与推动主要冲突。",
            "goal": "在当前情节中争取主动并推动剧情变化。",
        }
        for index, name in enumerate(sorted_names)
    ]


def infer_locations(chapters):
    keywords = ["会议室", "办公室", "餐厅", "街道", "学校", "医院", "车站", "房间", "大厅", "门口", "公司"]
    full_text = "\n".join(chapter["text"] for chapter in chapters)
    found = [word for word in keywords if word in full_text]
    if not found:
        found = ["主要场景", "转折场景"]

    return [
        {
            "id": f"loc_{index + 1:03d}",
            "name": name,
            "description": f"{name}，由小说文本中的叙事空间整理而来。",
        }
        for index, name in enumerate(found[:5])
    ]


def build_events(chapters, characters):
    events = []
    for chapter in chapters:
        sentences = split_sentences(chapter["text"])
        sample = sentences[:3] if sentences else [chapter["text"][:80]]
        events.append(
            {
                "id": f"event_{len(events) + 1:03d}",
                "source_chapter": chapter["chapter_id"],
                "summary": sample[0][:90],
                "conflict": (sample[1] if len(sample) > 1 else "人物关系出现变化")[:90],
                "emotional_shift": (sample[2] if len(sample) > 2 else "情绪从铺垫转向行动")[:90],
                "characters": [item["id"] for item in characters[: min(3, len(characters))]],
            }
        )
    return events
