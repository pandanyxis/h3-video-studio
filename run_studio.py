"""Render a story and up to nine reference pictures on the Studio Runpod."""
import argparse
import base64
import fcntl
import json
import mimetypes
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from export_project import export_project
from plan_story import validate_plan
from prepare_timeline import timeline_document

sys.path.insert(0, "/ComfyUI/custom_nodes/ComfyUI-H3-Continuity-Studio/examples")
from timeline_api import Client, atomic_json


def plan_with_writer(story, pictures):
    with urlopen("http://127.0.0.1:8190/h3studio/guides/Reference", timeout=30) as response:
        guide = json.load(response)["guide"]["content"]
    content = [{"type": "text", "text": story}]
    for slot, path in pictures:
        mime = mimetypes.guess_type(path.name)[0]
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.extend([
            {"type": "text", "text": f"Reference @{slot}"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ])
    payload = {
        "model": "qwen-hauhau",
        "response_format": {"type": "json_object"},
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": "Return only a JSON object "
             "with a shots array of exactly five consecutive short shots. Each shot needs "
             "nonempty action, camera, sound, start, end and prompt strings. Write each "
             "prompt as a self-contained MiniMax H3 video prompt using the Writer guidance. "
             "Use only the supplied logical @imageN labels when referring to the pictures. "
             "Keep characters and settings consistent across adjacent shots. "
             "Use the guide below for the prompt fields only.\n" + guide},
            {"role": "user", "content": content},
        ],
    }
    key = os.environ.get("LLAMA_API_KEY") or read_json(Path("/workspace/combined/access.json"))["LLAMA_API_KEY"]
    request = Request(
        "http://127.0.0.1:8080/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
    )
    with urlopen(request, timeout=900) as response:
        plan = json.loads(json.load(response)["choices"][0]["message"]["content"])
    shots = validate_plan(plan, 5)
    slots = {slot for slot, _ in pictures}
    used = set()
    for shot in shots:
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Writer returned an empty shot prompt")
        labels = set(re.findall(r"@(image\d+)\b", prompt))
        if not labels <= slots:
            raise ValueError("Writer returned an unknown picture slot")
        used.update(labels)
    if not used:
        raise ValueError("Writer did not use any reference pictures")
    return {"story": story, "shots": shots}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def produce(args):
    project = args.project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    clips = project / "clips"
    clips.mkdir(exist_ok=True)
    film = project / "film.mp4"
    client = Client("http://127.0.0.1:8188")
    receipt = project / "production.json"
    if receipt.exists():
        state = read_json(receipt)
    else:
        story = args.story.read_text(encoding="utf-8-sig").strip()
        if not story:
            raise ValueError("Story is empty")
        profile = next((p for p in client.capabilities()["profiles"] if p["id"] == args.profile), {})
        if not profile.get("ready"):
            raise RuntimeError(f"Continuity profile is unavailable: {profile}")
        pictures = [(f"image{index}", path) for index, path in enumerate(args.references, 17)]
        plan = plan_with_writer(story, pictures)
        body = timeline_document(plan, project.name, args.profile)
        for box, shot in zip(body["timeline"]["boxes"], plan["shots"]):
            box["prompt"] = shot["prompt"]
        for slot, path in pictures:
            asset = client.upload(path, "picture")
            body["assets"].append({"asset_id": asset["id"], "bindings": [
                {"kind": "picture", "slot": slot, "title": path.stem},
            ]})
        state = {"plan": plan, "document": body, "key": uuid.uuid4().hex}
        atomic_json(receipt, state)
    atomic_json(project / "shots.json", state["plan"])
    atomic_json(project / "timeline.json", state["document"])
    if film.exists():
        return film
    if "job_id" not in state:
        if "render" not in state:
            created = client.create(state["document"], state["key"])
            selection = {"expected_revision": created["revision"], "selection": {"kind": "all"}}
            render_plan = client.plan(created["id"], selection)
            state["render"] = dict(selection, plan_sha256=render_plan["plan_sha256"])
            state["project_id"] = created["id"]
            atomic_json(receipt, state)
        # Qwen sleeps after 10 seconds of inactivity, releasing its GPU allocation.
        time.sleep(12)
        job = client.render(state["project_id"], state["render"], state["key"])
        state["job_id"] = job["id"]
        atomic_json(receipt, state)
    print(f"Continuity job {state['job_id']}", flush=True)
    job = client.wait(state["job_id"], deadline=4 * 3600)
    artifacts = {item["unit_id"]: item for item in job["artifacts"] if item["kind"] == "box"}
    for index in range(1, 6):
        shot = f"shot-{index:02d}"
        client.download(artifacts[f"box:{shot}"], clips / f"{shot}.mp4")
    export_project(project, film)
    return film


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story", type=Path)
    parser.add_argument("project", type=Path)
    parser.add_argument("references", type=Path, nargs="+")
    parser.add_argument("--profile", default="studio-turbo8")
    args = parser.parse_args()
    if len(args.references) > 9:
        parser.error("Supply at most nine reference pictures")
    if any(mimetypes.guess_type(path.name)[0] not in {"image/jpeg", "image/png", "image/webp"}
           for path in args.references):
        parser.error("Reference pictures must be JPEG, PNG or WebP")
    with open("/workspace/.studio-render.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(produce(args))


if __name__ == "__main__":
    main()
