"""Studio's story planner and H3 prompt writer, powered by local Qwen."""
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

from plan_story import MAX_SHOT_SECONDS, PARAGRAPH_GUIDANCE, shot_count_instruction, validate_plan
from studio_storage import atomic_json, check_cancelled, progress, read_json


QWEN_URL = "http://127.0.0.1:8080"
MAX_WRITER_PROMPT = 12000
MAX_REFERENCE_NOTE = 1000
REFERENCE_ROLE_INSTRUCTIONS = (
    "Treat the user's notes on reference images as the assigned visual roles. Match named people to their "
    "described positions or features in the reference. When one image contains several subjects, distinguish "
    "them in the shot description and bind each relevant subject to its location in that @imageN reference. "
    "Apply a location, wardrobe, or style reference only to its assigned role. "
)
DIALOGUE_INSTRUCTIONS = (
    "Use English for production direction and for dialogue you are asked to write, unless another spoken "
    "language is requested. Copy supplied dialogue verbatim in quotes and preserve its language. "
    "For each line, name the speaker, place the line at a visible action in the shot, and fit it comfortably "
    "inside the shot duration. Keep the speaking face and mouth visible for on-screen speech when framing "
    "allows. Align the start and end of the audible words with that speaker's mouth movements. "
    "Use one speaker at a time unless overlap is explicitly requested. Keep other mouths at rest and "
    "keep ambience and music below the voice. Separate requested voice-over from on-screen speech. "
    "For shots without requested dialogue or narration, specify the relevant ambience and effects only, "
    "with no speech, crowd chatter, or mouth movements that imply speech. "
)
DEFAULT_WRITER_PROMPT = """Write precise English video direction for MiniMax H3. Preserve the user's intent and the planned action. Describe requested visible action directly and literally, without euphemisms or unrelated embellishment.

Describe each shot in time order: the starting pose and placement, the trigger for movement, how the action develops, and the final pose or position. Name the subject of each movement. For important gestures, specify the relevant limb or object, its direction and speed, what it contacts, and when it settles. Where relevant, describe weight shifts, planted feet, grip, gaze, and the physical reaction to contact. Fit the number of actions and their timing to the shot duration.

Keep subject identity, clothing, anatomy, object placement, scale, and screen direction consistent across frames and shots. State what remains steady when only part of the scene moves. Preserve the previous closing pose in the next opening when the action is continuous. Keep the movement physically coherent unless the story calls for a deliberate stylized effect.

For interactions, give each person a distinct identifier and keep it throughout the shot. Establish where each person is in the frame, whom they face, and the distance between them. Describe one person's action and the other person's response in order. At physical contact, name whose left or right limb moves, the exact point it touches on the other person, and how they hold or separate. If one person passes behind or obscures the other, state who is in front, which body parts remain visible, and how each person reappears. When framing is flexible, keep the key gestures and contact points visible and each person's silhouette distinct.

Describe framing and camera movement separately from subject movement. Specify a locked camera when the shot is static. Tie audible events to the action that produces them. Include dialogue only when requested. Focus on concrete details that affect the visible or audible result."""


def qwen_ready():
    with urlopen(QWEN_URL + "/health", timeout=3) as response:
        return response.status == 200


def read_reference_notes(project):
    path = Path(project) / "image-notes.json"
    return read_json(path) if path.is_file() else {}


def ask_qwen(instructions, text, pictures, *, clip_frames=(), reference_notes=None, timeout=900):
    content = [{"type": "text", "text": text}]
    notes = reference_notes or {}
    for slot, path in pictures:
        mime = mimetypes.guess_type(path.name)[0]
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        label = f"Reference @{slot}"
        if note := notes.get(path.name):
            label += f"\nUser's description and intended use: {note}"
        content.extend([
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ])
    for timestamp, path, *labels in clip_frames:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        label = labels[0] if labels else "Rendered clip"
        if timestamp is not None:
            label += f" frame at {timestamp:.2f} seconds"
        content.extend([
            {"type": "text", "text": f"{label}. This is review evidence."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
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
        with urlopen(request, timeout=timeout) as response:
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


class InvalidPlannerReferences(ValueError):
    pass


def planned_reference_slots(shot, slots):
    planned = shot.get("reference_slots")
    if planned is None:
        return []
    if isinstance(planned, list) and all(isinstance(slot, str) for slot in planned):
        normalized = [slot.strip().removeprefix("@") for slot in planned]
        if set(normalized) <= slots:
            return normalized
    labels = ", ".join(sorted(slots))
    raise InvalidPlannerReferences(f"The Planner returned invalid image references. Available labels: {labels}.")


def bind_shot_references(prompt, shot, slots, *, existing=()):
    used = referenced_slots(prompt)
    if not used <= slots:
        raise ValueError("Studio Writer returned an unknown picture slot")
    planned = planned_reference_slots(shot, slots)
    required = slots if len(slots) == 1 else set(planned)
    missing = (required | set(existing)) - used
    if missing:
        refs = ", ".join(f"@{slot}" for slot in sorted(missing))
        prompt += f" Where subjects or settings from {refs} appear, preserve their appearance and identity throughout the shot."
    return prompt


def suggest_revision(plan, index, feedback, pictures, writer_prompt, *, clip_frames=(), reference_notes=None):
    shots = plan["shots"]
    shot = shots[index]
    instructions = (
        "Revise only the selected English MiniMax H3 video prompt according to the user's feedback. "
        "Keep the shot duration, reference identities and continuity with the adjacent shots. "
        "Return a JSON object with a nonempty string named prompt. Use only supplied @imageN labels. "
        + REFERENCE_ROLE_INSTRUCTIONS + DIALOGUE_INSTRUCTIONS
    )
    if clip_frames:
        instructions += (
            "Review the rendered frames against the requested shot, reference images and feedback. "
            "Also return a short string named observations describing what the sampled frames show and what you changed. "
            "If adjacent boundary frames are supplied, use the previous shot's last frame to anchor the revised "
            "opening and the next shot's first frame to anchor the revised ending. Account for each planned cut. "
            "Distinguish visible evidence from uncertainty. Do not infer sounds or action between sampled frames. "
            "Use rendered frames as evidence only; never refer to them as H3 image slots. "
        )
    written = ask_qwen(
        instructions + "\n\n" + writer_prompt,
        json.dumps({
            "story": plan["story"], "previous": shots[index - 1] if index else None,
            "shot": shot, "next": shots[index + 1] if index + 1 < len(shots) else None,
            "feedback": feedback,
        }, ensure_ascii=False), pictures, clip_frames=clip_frames, reference_notes=reference_notes,
    )
    prompt = written.get("prompt") if isinstance(written, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("The Writer did not return a revised prompt for this shot.")
    slots = {slot for slot, _ in pictures}
    prompt = bind_shot_references(prompt.strip(), shot, slots, existing=referenced_slots(shot["prompt"]))
    observations = written.get("observations")
    return {"prompt": prompt, "observations": observations.strip() if isinstance(observations, str) else ""}


def revise_shot(plan, index, feedback, pictures, writer_prompt, *, reference_notes=None):
    plan["shots"][index]["prompt"] = suggest_revision(
        plan, index, feedback, pictures, writer_prompt, reference_notes=reference_notes)["prompt"]


def plan_and_write(story, pictures, project=None, writer_prompt=DEFAULT_WRITER_PROMPT):
    notes = read_reference_notes(project) if project else {}
    if project:
        check_cancelled(project)
        progress(project, "planning")
    saved = Path(project) / "plan.json" if project else None
    slots = {slot for slot, _ in pictures}
    labels = ", ".join(slot for slot, _ in pictures)
    planner_instructions = (
        "You are Studio's story and shot planner. The user supplies a rough shot idea "
        "or a story, not technical video prompts. " + shot_count_instruction() + PARAGRAPH_GUIDANCE +
        "Return only a JSON object with a shots array in story order. "
        "Each shot needs nonempty action, camera, sound, start and end strings. "
        "Use one or two concise sentences per field. "
        f"Set a numeric duration_seconds from 1 to {MAX_SHOT_SECONDS} for each shot, based on the action's "
        "pacing. Use shorter clips for quick actions and longer clips for actions that need time. "
        "Plan the story as a chain of beats. Establish the opening situation, carry each action or "
        "reaction forward from what just happened, and reach the ending implied or requested by the user. "
        "Keep the user's sequence, causal links and payoff. When a beat needs more than ten seconds, "
        "split it at a clear point in the action. Continue that action from the split without replaying "
        "its setup, reintroducing the characters, or jumping ahead to an unrelated event. "
        "The cut flag describes how THIS shot starts. Set cut to false for continuous action. "
        "Set it to true for a distinct scene or when you plan a hard camera cut, even within the same scene. "
        "In sound, record the requested dialogue and narration with their language, speaker and exact words. "
        "Write new spoken lines in English unless the user requests another language. "
        "Allow enough time for natural delivery. Split dialogue between complete lines, not within a line. "
        "When speech is not requested, plan ambience and effects only. "
        "Ground character identity, clothing, objects and locations in the reference images. "
        + REFERENCE_ROLE_INSTRUCTIONS +
        f"For every shot, include a reference_slots array. The only allowed values are: {labels}. "
        "Copy these labels exactly, without the @ prefix. Do not renumber them based on upload order. "
        "List the references for every person, object or setting that appears in that shot, even if it "
        "appeared in the previous shot. Use an empty array only for a deliberate cutaway with no relevant reference. "
        "For interactions, establish each person's position and orientation and assign each gesture, "
        "point of contact and reaction to a specific person. "
        "Preserve the user's intent and character identities. Use only supplied @imageN "
        "labels for visual references. Connect each shot's opening state to the previous "
        "closing state when the action is continuous. Make each start and end describe visible "
        "positions relative to fixed landmarks, body pose, movement in progress, and relevant objects. "
        "Keep actions feasible in a short clip."
    )
    plan = read_json(saved) if saved and saved.is_file() else None
    retry_instruction = ""
    for attempt in range(2):
        resumed = plan is not None
        if not resumed:
            if project:
                check_cancelled(project)
            plan = ask_qwen(planner_instructions + retry_instruction, story, pictures, reference_notes=notes)
        shots = validate_plan(plan, require_duration=not resumed)
        try:
            for shot in shots:
                if "reference_slots" in shot:
                    shot["reference_slots"] = planned_reference_slots(shot, slots)
        except InvalidPlannerReferences as error:
            if attempt == 1:
                raise
            plan = None
            retry_instruction = f" Correct the reference_slots arrays. {error}"
        else:
            break
    if saved:
        atomic_json(saved, plan)
        check_cancelled(project)
        progress(project, "writing", total=len(shots))
    writer_shots = []
    for index, shot in enumerate(shots):
        context = {}
        if index and not shot.get("cut", False):
            context["previous_closing_state"] = shots[index - 1]["end"]
        if index + 1 < len(shots) and not shots[index + 1].get("cut", False):
            context["next_opening_state"] = shots[index + 1]["start"]
        writer_shots.append({**shot, **context})
    written = ask_qwen(
        "Convert the supplied shot plan into complete English video prompts in the same order. "
        f"Return only a JSON object with a prompts array of exactly {len(shots)} nonempty strings. "
        "Write one prompt per planned shot and fit its movement and sounds into duration_seconds. "
        "Each string must be a self-contained prompt, never a list of suggestions or placeholders. "
        "Write the prompts as consecutive parts of the same story. If previous_closing_state is present, "
        "begin at that visible state and continue the movement or reaction already underway. Preserve "
        "who stands where, what they hold, and what has already happened. If next_opening_state is present, "
        "end this clip in a state that leads into it. Use adjacent states only to anchor the boundary. "
        "Keep the actual action within this shot's planned beat and avoid replaying prior beats. "
        "Use supplied @imageN labels to bind the relevant visual references to subjects. "
        + REFERENCE_ROLE_INSTRUCTIONS +
        "Include every reference_slots entry for this shot with an @ prefix, even if it was used in a previous shot. "
        "Use only the supplied labels. " + DIALOGUE_INSTRUCTIONS +
        "Follow these writing instructions within that output format:\n\n"
        + writer_prompt,
        json.dumps({"story": story, "shots": writer_shots}, ensure_ascii=False), pictures, reference_notes=notes,
    )
    prompts = written.get("prompts") if isinstance(written, dict) else None
    if not isinstance(prompts, list) or len(prompts) != len(shots):
        raise ValueError(f"Studio Writer must return exactly {len(shots)} prompts")
    used = set()
    for shot, prompt in zip(shots, prompts):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Studio Writer returned an empty prompt")
        shot["prompt"] = bind_shot_references(prompt.strip(), shot, slots)
        used.update(referenced_slots(shot["prompt"]))
    if not used:
        raise ValueError("Studio Writer did not use the reference pictures")
    return {"story": story, "shots": shots}
