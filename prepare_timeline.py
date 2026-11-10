"""Prepare a managed Continuity Timeline document from a saved shot plan."""
import argparse
import json
from pathlib import Path

from plan_story import validate_plan


def timeline_document(plan, name, profile):
    count = len(plan["shots"])
    if count not in (5, 10, 20):
        raise ValueError("Expected 5, 10 or 20 shots")
    shots = validate_plan(plan, count)
    boxes = []
    for index, shot in enumerate(shots, 1):
        prompt = "\n".join((
            f"Opening state: {shot['start']}",
            f"Action: {shot['action']}",
            f"Camera: {shot['camera']}",
            f"Sound: {shot['sound']}",
            f"Closing state: {shot['end']}",
        ))
        boxes.append({
            "id": f"shot-{index:02d}",
            "frames": 90 if index == 1 else 68,
            "seed": 40 + index,
            "prompt": prompt,
        })
    return {
        "name": name,
        "profile": profile,
        "settings": {"width": 768, "height": 448, "context_frames": 90,
                     "carry_audio": True, "ref_image_size": "match"},
        "timeline": {"version": 1, "boxes": boxes},
        "assets": [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    document = timeline_document(plan, args.name, args.profile)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(document, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
