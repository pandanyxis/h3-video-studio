"""Create a structured shot plan using an OpenAI-compatible endpoint."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

MAX_SHOTS = 20
MAX_SHOT_SECONDS = 10
SHOT_FIELDS = ("action", "camera", "sound", "start", "end")
PARAGRAPH_GUIDANCE = (
    "When the supplied story contains paragraph breaks, use them as cues to reassess what the viewer "
    "should see or hear. When there are no paragraph breaks, plan directly from the story's beats. "
    "Look for changes in the main action, speaker, point of view, time, or location. "
    "If the focus shifts within a continuous action, consider reframing within the shot. "
    "Use a new shot when a cut clarifies the change or gives a meaningful reaction its own frame. "
    "Keep the same shot across paragraphs when the action and framing still work, and split a single "
    "paragraph when it contains distinct beats that need separate shots. Use descriptive backstory to "
    "inform relevant shots, and show it as a separate scene only when the story calls for it. "
)
EFFECT_GUIDANCE = (
    "Preserve the story's visible effects and transformations in the action field. Describe what "
    "triggers each effect, where it starts, how it develops, and what it reveals or leaves behind. "
    "Record any intended accompanying sound in the sound field and carry visible effects through "
    "the opening and closing states when they continue across shots. "
)


def shot_count_instruction(count=None):
    if count is not None:
        return f"Plan exactly {count} consecutive shots for the supplied idea or story. "
    return (
        f"Choose the fewest shots that fully tell the supplied idea or story, from 1 to {MAX_SHOTS}. "
        "Use one shot for a simple continuous action that fits in one clip. Split into more shots "
        "when distinct story beats, camera cuts, time or location changes, or the duration limit require it. "
        "Do not pad simple ideas with filler shots or force a fixed count. "
    )


def validate_plan(plan, count=None, *, require_duration=False):
    shots = plan.get("shots") if isinstance(plan, dict) else None
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        raise ValueError(f"Expected a plan with 1 to {MAX_SHOTS} shots")
    if count is not None and len(shots) != count:
        raise ValueError(f"Expected exactly {count} shots")
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict) or any(
            not isinstance(shot.get(field), str) or not shot[field].strip()
            for field in SHOT_FIELDS
        ):
            raise ValueError(f"Shot {index} is missing required descriptions")
        if require_duration or "duration_seconds" in shot:
            duration = shot.get("duration_seconds")
            if type(duration) not in (int, float) or not 1 <= duration <= MAX_SHOT_SECONDS:
                raise ValueError(f"Shot {index} needs a duration from 1 to {MAX_SHOT_SECONDS} seconds")
        if "cut" in shot and type(shot["cut"]) is not bool:
            raise ValueError(f"Shot {index} needs a boolean cut value")
    return shots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--shots", type=int, choices=range(1, MAX_SHOTS + 1),
                        help="Override the automatic shot count")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="qwen-hauhau")
    args = parser.parse_args()

    story = args.story.read_text(encoding="utf-8-sig").strip()
    if not story:
        parser.error("The story is empty")
    if args.output.exists():
        parser.error("The output file already exists")

    headers = {"Content-Type": "application/json"}
    key = os.environ.get("LLAMA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": (
                shot_count_instruction(args.shots) + PARAGRAPH_GUIDANCE + EFFECT_GUIDANCE +
                "Return only a JSON object with a shots array. Each shot must contain "
                "nonempty strings: action, camera, sound, start, end. "
                "Preserve character, clothing and setting details across connected shots. "
                "Describe each opening and closing state. Add a numeric duration_seconds between 1 "
                f"and {MAX_SHOT_SECONDS} for each shot, chosen to fit its action."
            )},
            {"role": "user", "content": story},
        ],
    }
    request = Request(
        args.endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urlopen(request, timeout=300) as response:
        content = json.load(response)["choices"][0]["message"]["content"]
    shots = validate_plan(json.loads(content), args.shots, require_duration=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump({"story": story, "shots": shots}, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

