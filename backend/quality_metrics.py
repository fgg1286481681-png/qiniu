def ratio(numerator, denominator):
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 4)


def calculate_quality_metrics(script, validation, repair_count=0):
    chapters = script.get("chapters", [])
    events = script.get("events", [])
    scenes = script.get("scenes", [])
    character_ids = {item.get("id") for item in script.get("characters", [])}
    location_ids = {item.get("id") for item in script.get("locations", [])}
    chapter_ids = {item.get("id") for item in chapters}
    event_ids = {item.get("id") for item in events}

    covered_chapters = set()
    covered_events = set()
    element_count = 0
    dialogue_count = 0
    complete_scene_count = 0
    valid_references = 0
    total_references = 0

    for event in events:
        total_references += 1
        valid_references += int(event.get("source_chapter") in chapter_ids)
        for character_id in event.get("characters", []):
            total_references += 1
            valid_references += int(character_id in character_ids)

    for scene in scenes:
        elements = scene.get("elements", [])
        element_count += len(elements)
        complete_scene_count += int(len(elements) >= 3)

        location_id = scene.get("heading", {}).get("location_id")
        total_references += 1
        valid_references += int(location_id in location_ids)

        for chapter_id in scene.get("source_chapters", []):
            total_references += 1
            if chapter_id in chapter_ids:
                valid_references += 1
                covered_chapters.add(chapter_id)

        for character_id in scene.get("characters", []):
            total_references += 1
            valid_references += int(character_id in character_ids)

        for element in elements:
            if element.get("type") == "dialogue":
                dialogue_count += 1
                total_references += 1
                valid_references += int(element.get("character_id") in character_ids)
            event_id = element.get("event_id")
            if event_id:
                total_references += 1
                if event_id in event_ids:
                    valid_references += 1
                    covered_events.add(event_id)

    ai_review = validation.get("ai_review") or {}
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for issue in ai_review.get("issues", []):
        severity = issue.get("severity")
        if severity in severity_counts:
            severity_counts[severity] += 1

    return {
        "chapter_coverage": ratio(len(covered_chapters), len(chapter_ids)),
        "event_coverage": ratio(len(covered_events), len(event_ids)),
        "dialogue_ratio": ratio(dialogue_count, element_count),
        "reference_consistency": ratio(valid_references, total_references),
        "scene_completeness": ratio(complete_scene_count, len(scenes)),
        "schema_valid": bool(validation.get("valid")),
        "ai_review": {
            "score": ai_review.get("score"),
            "severity_counts": severity_counts,
            "requires_rewrite": bool(ai_review.get("requires_rewrite")),
        },
        "repair_triggered": repair_count > 0,
        "repair_count": repair_count,
    }
