"""Create a structured shot plan using an OpenAI-compatible endpoint."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

MAX_SHOT_SECONDS = 10
LEGACY_MAX_SHOT_SECONDS = 15
SHOT_FIELDS = ("action", "camera", "sound", "start", "end")
PARAGRAPH_GUIDANCE = (
    "When the supplied story contains paragraph breaks, use them as cues to reassess what the viewer "
    "should see or hear. When there are no paragraph breaks, plan directly from the story's beats. "
    "Read each paragraph in story context. Resolve who or what each description refers to and when it "
    "applies. "
    "For simultaneous events in different locations, look for English cues such as 'while', "
    "'meanwhile', and 'at the same time'. Resolve their meaning from the verbs and surrounding context. "
    "A 'while' clause may describe earlier background, simultaneous action in the same place, "
    "or a parallel event elsewhere. When both events are meant to be shown, plan "
    "a distinct shot for each location even if they share a paragraph or sentence. Apply the same "
    "rule to successive events in different locations within one paragraph. Use a camera cut "
    "at each location change. Preserve the story's timing, including any overlap. "
    "Distinguish the state already in effect when a beat begins, the action that happens on "
    "screen, and the resulting state. Put clearly stated or implied existing conditions into start, "
    "visible changes into action, and their results into end. A description of a current state does "
    "not require inventing an action that caused it. Keep memories, hypotheticals, and off-screen "
    "background separate from the current visible scene unless the story calls for showing them. "
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
CONTINUITY_GUIDANCE = (
    "Track each character by name across the story. Preserve their identity and current appearance, "
    "including any transformation, until the story changes them again. Record each visible character's "
    "pose, position, orientation, and held objects in start and end where they matter. Also track changes "
    "to important props and the set, such as an object being moved or a door being left open. "
    "Continue directly from the last state when a cut changes only the camera angle. After a time or "
    "location change, establish the new pose and positions while carrying forward lasting changes. "
    "When a character or object returns, use its last established state unless the story gives a reason to change it. "
    "Record explicit clothing actions in action, naming the character, garment, and visible change. "
    "In start and end, state the relevant wardrobe for each visible character, including garment "
    "position and condition, layers, footwear, and accessories when relevant. Track each change "
    "forward through later shots, including after camera cuts or when a character returns to frame. "
    "Keep garments that stay on, and preserve changes such as an opened coat, rolled sleeves, "
    "or a torn jacket until a later story event changes them. Use reference images as the wardrobe "
    "baseline where applicable, then follow the wardrobe established by the story. "
    "Treat a clearly stated or unambiguously implied absence of clothing as a wardrobe state. "
    "Record whether a specific garment is absent or the character is unclothed. Carry that state "
    "forward until the story establishes a change. When the text is ambiguous, keep the established wardrobe. "
    "When a scene opens with an action that clearly requires a changed garment state, record that "
    "state in start. Include a clothing change in action only if it happens on screen in this shot. "
)


def shot_count_instruction(count=None):
    if count is not None:
        return f"Plan exactly {count} consecutive shots for the supplied idea or story. "
    return (
        "Plan as many shots as needed that fully tell the story at a natural pace. "
        "Estimate how long movement, dialogue, and reactions take before deciding what fits in each shot. "
        "Allow time for actions to start, develop, and settle, and for any meaningful pause or response. "
        f"If a beat needs more than {MAX_SHOT_SECONDS} seconds, split it at a natural pause, action boundary, "
        "or reaction. Continue from that state and preserve the full sequence at its natural pace. "
        "Use one shot for a simple continuous action that fits in one clip. Split into more shots "
        "when distinct story beats, camera cuts, time or location changes, or the duration limit require it. "
        "Do not pad simple ideas with filler shots or force a fixed count. "
    )


def validate_plan(plan, count=None, *, require_duration=False, max_duration=MAX_SHOT_SECONDS):
    shots = plan.get("shots") if isinstance(plan, dict) else None
    if not isinstance(shots, list) or not shots:
        raise ValueError("Expected a plan with at least one shot")
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
            if type(duration) not in (int, float) or not 1 <= duration <= max_duration:
                raise ValueError(f"Shot {index} needs a duration from 1 to {max_duration} seconds")
        if "cut" in shot and type(shot["cut"]) is not bool:
            raise ValueError(f"Shot {index} needs a boolean cut value")
    return shots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--shots", type=int,
                        help="Override the automatic shot count")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="qwen-hauhau")
    args = parser.parse_args()
    if args.shots is not None and args.shots < 1:
        parser.error("--shots must be positive")

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
                shot_count_instruction(args.shots) + PARAGRAPH_GUIDANCE + EFFECT_GUIDANCE + CONTINUITY_GUIDANCE +
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

