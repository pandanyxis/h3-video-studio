"""Studio's story planner and H3 prompt writer, powered by local Qwen."""
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

from plan_story import validate_plan
from studio_storage import atomic_json, progress, read_json


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
        access = json.loads(Path("/workspace/combined/access.json").read_text(encoding="utf-8"))
        key = access["LLAMA_API_KEY"]
    payload = {
        "model": "qwen-hauhau",
        "response_format": {"type": "json_object"},
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ],
    }
    request = Request(
        "http://127.0.0.1:8080/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    with urlopen(request, timeout=900) as response:
        return json.loads(json.load(response)["choices"][0]["message"]["content"])


def plan_and_write(story, pictures, project=None):
    if project:
        progress(project, "planning")
    saved = Path(project) / "plan.json" if project else None
    plan = read_json(saved) if saved and saved.is_file() else ask_qwen(
        "You are Studio's story and shot planner. The user supplies a rough shot idea "
        "or a story, not technical video prompts. Expand it into exactly five coherent, "
        "short consecutive shots. Return only a JSON object with a shots array. "
        "Each shot needs nonempty action, camera, sound, start and end strings. "
        "Ground character identity, clothing, objects and locations in the reference images. "
        "Preserve the user's intent and character identities. Use only supplied @imageN "
        "labels for visual references. Connect each shot's opening state to the previous "
        "closing state when the action is continuous. Keep actions feasible in a short clip.",
        story, pictures,
    )
    shots = validate_plan(plan, 5)
    if saved:
        atomic_json(saved, plan)
        progress(project, "writing")
    written = ask_qwen(
        "You are Studio's MiniMax H3 prompt writer. Convert the supplied five-shot plan "
        "into five complete, production-ready English video prompts in the same order. "
        "Return only a JSON object with a prompts array of exactly five nonempty strings. "
        "Each string must be a self-contained prompt, never a list of suggestions or placeholders. "
        "Preserve the plan's action, camera, sound, opening state and closing state. "
        "Describe concrete visible movement chronologically, with clear subjects, framing, "
        "camera movement, environment and audible sound. Keep dialogue only when requested. "
        "Respect reference identities, clothing, props, screen direction and scene continuity. "
        "Use supplied @imageN labels to bind the relevant visual references to subjects. "
        "Do not introduce unknown labels, new characters, unrelated actions or unexplained cuts. "
        "The user must not need to supply or edit technical prompts.",
        json.dumps({"story": story, "shots": shots}, ensure_ascii=False), pictures,
    )
    prompts = written.get("prompts") if isinstance(written, dict) else None
    if not isinstance(prompts, list) or len(prompts) != len(shots):
        raise ValueError("Studio Writer must return exactly five prompts")
    slots = {slot for slot, _ in pictures}
    used = set()
    for shot, prompt in zip(shots, prompts):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Studio Writer returned an empty prompt")
        labels = set(re.findall(r"@(image\d+)\b", prompt))
        if not labels <= slots:
            raise ValueError("Studio Writer returned an unknown picture slot")
        used.update(labels)
        shot["prompt"] = prompt.strip()
    if not used:
        raise ValueError("Studio Writer did not use the reference pictures")
    return {"story": story, "shots": shots}
