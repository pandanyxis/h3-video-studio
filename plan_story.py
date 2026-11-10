"""Create a structured shot plan using an OpenAI-compatible endpoint."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

SHOT_FIELDS = ("action", "camera", "sound", "start", "end")


def validate_plan(plan, count):
    shots = plan.get("shots") if isinstance(plan, dict) else None
    if not isinstance(shots, list) or len(shots) != count:
        raise ValueError(f"Expected exactly {count} shots")
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict) or any(
            not isinstance(shot.get(field), str) or not shot[field].strip()
            for field in SHOT_FIELDS
        ):
            raise ValueError(f"Shot {index} is missing required descriptions")
    return shots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--shots", type=int, choices=(5, 10, 20), default=5)
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
                f"Plan exactly {args.shots} consecutive video shots for the supplied story. "
                "Return only a JSON object with a shots array. Each shot must contain "
                "nonempty strings: action, camera, sound, start, end. "
                "Preserve character, clothing and setting details across connected shots. "
                "Describe the opening and closing visual state of every shot."
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
    shots = validate_plan(json.loads(content), args.shots)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump({"story": story, "shots": shots}, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

