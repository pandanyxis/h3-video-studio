"""Studio's story planner and H3 prompt writer, powered by local Qwen."""
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

from plan_story import MAX_SHOT_SECONDS, shot_count_instruction, validate_plan
from studio_storage import atomic_json, check_cancelled, progress, read_json


QWEN_URL = "http://127.0.0.1:8080"
MAX_WRITER_PROMPT = 12000
DEFAULT_WRITER_PROMPT = """Write precise English video direction for MiniMax H3. Preserve the user's intent and the planned action. Describe requested visible action directly and literally, without euphemisms or unrelated embellishment.

Describe each shot in time order: the starting pose and placement, the trigger for movement, how the action develops, and the final pose or position. Name the subject of each movement. For important gestures, specify the relevant limb or object, its direction and speed, what it contacts, and when it settles. Where relevant, describe weight shifts, planted feet, grip, gaze, and the physical reaction to contact. Fit the number of actions and their timing to the shot duration.

Keep subject identity, clothing, anatomy, object placement, scale, and screen direction consistent across frames and shots. State what remains steady when only part of the scene moves. Preserve the previous closing pose in the next opening when the action is continuous. Keep the movement physically coherent unless the story calls for a deliberate stylized effect.

For interactions, give each person a distinct identifier and keep it throughout the shot. Establish where each person is in the frame, whom they face, and the distance between them. Describe one person's action and the other person's response in order. At physical contact, name whose left or right limb moves, the exact point it touches on the other person, and how they hold or separate. If one person passes behind or obscures the other, state who is in front, which body parts remain visible, and how each person reappears. When framing is flexible, keep the key gestures and contact points visible and each person's silhouette distinct.

Describe framing and camera movement separately from subject movement. Specify a locked camera when the shot is static. Tie audible events to the action that produces them. Include dialogue only when requested. Focus on concrete details that affect the visible or audible result."""


def qwen_ready():
    with urlopen(QWEN_URL + "/health", timeout=3) as response:
        return response.status == 200


def ask_qwen(instructions, text, pictures):
    content = [{"type": "text", "text": text}]
    for slot, path in pictures:
        mime = mimetypes.guess_type(path.name)[0]
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.extend([
            {"type": "text", "text": f"Reference @{slot}"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ])
    key = os.environ.get("LLAMA_API_KEY")
    if not key:
        access = Path("/workspace/combined/access.json")
        if not access.is_file():
            raise RuntimeError("Set LLAMA_API_KEY to the same key as the GPU host and resume the project.")
        key = read_json(access)["LLAMA_API_KEY"]
    payload = {
        "model": "qwen-hauhau",
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.7,
        "top_p": 0.8,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ],
    }
    for max_tokens in (8192, 16384):
        request = Request(
            QWEN_URL + "/v1/chat/completions",
            data=json.dumps(dict(payload, max_tokens=max_tokens)).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        with urlopen(request, timeout=900) as response:
            choice = json.load(response)["choices"][0]
        if choice.get("finish_reason") == "length":
            continue
        try:
            return json.loads(choice["message"]["content"])
        except (TypeError, ValueError):
            raise ValueError("Qwen returned an invalid shot plan or invalid prompts. Resume the project to try again.") from None
    raise ValueError("Qwen reached its output limit twice. Resume the project or try a shorter story.")


def referenced_slots(text):
    return set(re.findall(r"@(image\d+)\b", text))


def bind_shot_references(prompt, shot, slots, *, existing=()):
    used = referenced_slots(prompt)
    if not used <= slots:
        raise ValueError("Studio Writer returned an unknown picture slot")
    planned = shot.get("reference_slots")
    if planned is None:
        planned = []
    if not isinstance(planned, list) or any(
            not isinstance(slot, str) or slot not in slots for slot in planned):
        raise ValueError("The Planner returned invalid image references for a shot.")
    required = slots if len(slots) == 1 else set(planned)
    missing = (required | set(existing)) - used
    if missing:
        refs = ", ".join(f"@{slot}" for slot in sorted(missing))
        prompt += f" Where subjects or settings from {refs} appear, preserve their appearance and identity throughout the shot."
    return prompt


def revise_shot(plan, index, feedback, pictures, writer_prompt):
    shots = plan["shots"]
    shot = shots[index]
    written = ask_qwen(
        "Revise only the selected English MiniMax H3 video prompt according to the user's feedback. "
        "Keep the shot duration, reference identities and continuity with the adjacent shots. "
        "Return a JSON object with one nonempty string named prompt. Use only supplied @imageN labels.\n\n"
        + writer_prompt,
        json.dumps({
            "story": plan["story"], "previous": shots[index - 1] if index else None,
            "shot": shot, "next": shots[index + 1] if index + 1 < len(shots) else None,
            "feedback": feedback,
        }, ensure_ascii=False), pictures,
    )
    prompt = written.get("prompt") if isinstance(written, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("The Writer did not return a revised prompt for this shot.")
    slots = {slot for slot, _ in pictures}
    shot["prompt"] = bind_shot_references(
        prompt.strip(), shot, slots, existing=referenced_slots(shot["prompt"]))


def plan_and_write(story, pictures, project=None, writer_prompt=DEFAULT_WRITER_PROMPT):
    if project:
        check_cancelled(project)
        progress(project, "planning")
    saved = Path(project) / "plan.json" if project else None
    resumed = saved and saved.is_file()
    plan = read_json(saved) if resumed else ask_qwen(
        "You are Studio's story and shot planner. The user supplies a rough shot idea "
        "or a story, not technical video prompts. " + shot_count_instruction() +
        "Return only a JSON object with a shots array in story order. "
        "Each shot needs nonempty action, camera, sound, start and end strings. "
        "Use one or two concise sentences per field. "
        f"Set a numeric duration_seconds from 1 to {MAX_SHOT_SECONDS} for each shot, based on the action's "
        "pacing. Use shorter clips for quick actions and longer clips for actions that need time. "
        "Split any beat that cannot fit within ten seconds. Set cut to true only where the next shot "
        "starts a distinct scene or the story calls for a hard camera cut. "
        "Ground character identity, clothing, objects and locations in the reference images. "
        "For every shot, include a reference_slots array of supplied imageN labels, without the @ prefix. "
        "List the references for every person, object or setting that appears in that shot, even if it "
        "appeared in the previous shot. Use an empty array only for a deliberate cutaway with no relevant reference. "
        "For interactions, establish each person's position and orientation and assign each gesture, "
        "point of contact and reaction to a specific person. "
        "Preserve the user's intent and character identities. Use only supplied @imageN "
        "labels for visual references. Connect each shot's opening state to the previous "
        "closing state when the action is continuous. Keep actions feasible in a short clip.",
        story, pictures,
    )
    shots = validate_plan(plan, require_duration=not resumed)
    if saved:
        atomic_json(saved, plan)
        check_cancelled(project)
        progress(project, "writing", total=len(shots))
    written = ask_qwen(
        "Convert the supplied shot plan into complete English video prompts in the same order. "
        f"Return only a JSON object with a prompts array of exactly {len(shots)} nonempty strings. "
        "Write one prompt per planned shot and fit its movement and sounds into duration_seconds. "
        "Each string must be a self-contained prompt, never a list of suggestions or placeholders. "
        "Use supplied @imageN labels to bind the relevant visual references to subjects. "
        "Include every reference_slots entry for this shot with an @ prefix, even if it was used in a previous shot. "
        "Use only the supplied labels. Follow these writing instructions within that output format:\n\n"
        + writer_prompt,
        json.dumps({"story": story, "shots": shots}, ensure_ascii=False), pictures,
    )
    prompts = written.get("prompts") if isinstance(written, dict) else None
    if not isinstance(prompts, list) or len(prompts) != len(shots):
        raise ValueError(f"Studio Writer must return exactly {len(shots)} prompts")
    slots = {slot for slot, _ in pictures}
    used = set()
    for shot, prompt in zip(shots, prompts):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Studio Writer returned an empty prompt")
        shot["prompt"] = bind_shot_references(prompt.strip(), shot, slots)
        used.update(referenced_slots(shot["prompt"]))
    if not used:
        raise ValueError("Studio Writer did not use the reference pictures")
    return {"story": story, "shots": shots}
