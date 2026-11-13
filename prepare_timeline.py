"""Prepare a managed Continuity Timeline document from a saved shot plan."""
import argparse
import json
from pathlib import Path

from plan_story import LEGACY_MAX_SHOT_SECONDS, MAX_SHOT_SECONDS, validate_plan

FPS = 24
ASPECT_SIZES = {"16:9": (1024, 576), "9:16": (576, 1024)}
FRAME_GRID = 17
H3_MAX_RENDER_FRAMES = 362


def cap_pending_shot_frames(document, completed=0):
    """Apply the clip duration cap and fit carried context into H3's render window."""
    context = document["settings"]["context_frames"]
    boxes = document["timeline"]["boxes"]
    for index in range(completed, len(boxes)):
        box = boxes[index]
        fresh = index == 0 or box["cut"]
        limit = shot_frames(MAX_SHOT_SECONDS, fresh)
        if not fresh:
            limit = min(limit, ((H3_MAX_RENDER_FRAMES - context) // FRAME_GRID) * FRAME_GRID)
        box["frames"] = min(box["frames"], limit)


def shot_frames(duration, fresh):
    """Pick a native H3 length that stays within the clip duration cap."""
    if duration is None:
        return 90 if fresh else 68
    offset = 5 if fresh else 0
    steps = max(1, round((duration * FPS - offset) / FRAME_GRID))
    limit = (MAX_SHOT_SECONDS * FPS - offset) // FRAME_GRID
    return min(steps, limit) * FRAME_GRID + offset


def timeline_document(plan, name, profile, aspect=None):
    width, height = ASPECT_SIZES[aspect] if aspect else (768, 448)
    shots = validate_plan(plan, max_duration=LEGACY_MAX_SHOT_SECONDS)
    boxes = []
    for index, shot in enumerate(shots, 1):
        prompt = "\n".join((
            f"Opening state: {shot['start']}",
            f"Action: {shot['action']}",
            f"Camera: {shot['camera']}",
            f"Sound: {shot['sound']}",
            f"Closing state: {shot['end']}",
        ))
        cut = shot.get("cut", False)
        boxes.append({
            "id": f"shot-{index:02d}",
            "frames": shot_frames(shot.get("duration_seconds"), fresh=index == 1 or cut),
            "cut": cut,
            "seed": 40 + index,
            "prompt": prompt,
        })
    # Continuation after a scene's first shot needs enough frames to carry forward.
    fresh_lengths = [box["frames"] for index, box in enumerate(boxes[:-1])
                     if (index == 0 or box["cut"]) and not boxes[index + 1]["cut"]]
    context_limit = min(fresh_lengths, default=90)
    context = max(value for value in (22, 39, 90) if value <= context_limit)
    document = {
        "name": name,
        "profile": profile,
        "consumer": {"studio_exports_clips": True},
        "settings": {"width": width, "height": height, "context_frames": context,
                     "carry_audio": True, "ref_image_size": "match"},
        "timeline": {"version": 1, "boxes": boxes},
        "assets": [],
    }
    cap_pending_shot_frames(document)
    return document


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
