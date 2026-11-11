"""Produce the planned shots and a complete film on the Studio GPU host."""
import argparse
import mimetypes
import re
import time
import uuid
from pathlib import Path

from export_project import export_project
from prepare_timeline import ASPECT_SIZES, timeline_document
from studio_render import RenderClient, RenderError
from studio_storage import atomic_json, gpu_lock, progress, read_json
from studio_writer import DEFAULT_WRITER_PROMPT, plan_and_write, qwen_ready, revise_shot


def wait_for_models(client, project, profile_id, *, needs_qwen):
    progress(project, "preparing")
    until = time.monotonic() + 90 * 60
    while True:
        try:
            profiles = client.capabilities()["profiles"]
            profile = next((item for item in profiles if item["id"] == profile_id), {})
            if profile.get("ready") and (not needs_qwen or qwen_ready()):
                return
        except (OSError, ValueError, RenderError):
            pass
        if time.monotonic() >= until:
            raise RuntimeError("De videomodellen zijn nog niet beschikbaar. Controleer de GPU-host en hervat het project.")
        time.sleep(5)


def prepare_revision(project, revision, pictures, writer_prompt):
    parent = read_json(project.parent / revision["parent"] / "production.json")
    index = revision["shot"]
    plan, document = parent["plan"], parent["document"]
    progress(project, "writing", total=len(plan["shots"]))
    revise_shot(plan, index, revision["feedback"], pictures, writer_prompt)
    document["timeline"]["boxes"][index]["prompt"] = plan["shots"][index]["prompt"]
    return {"plan": plan, "document": document, "project_id": parent["project_id"],
            "selection": {"kind": "boxes", "ids": [f"shot-{index + 1:02d}"], "rerender": True},
            "key": uuid.uuid4().hex}


def prepare_render(client, state, receipt):
    document = state["document"]
    if "selection" in state:
        current = client.project(state["project_id"])
        expected = state.setdefault("base_revision", current["revision"])
        atomic_json(receipt, state)
        if current["revision"] == expected:
            current = client.update_project(state["project_id"], document, expected)
        elif current["revision"] != expected + 1 or current["timeline"] != document["timeline"]:
            raise RenderError("De renderdienst heeft inmiddels een andere revisie verwerkt.")
    else:
        current = client.create(document, state["key"])
        state["project_id"] = current["id"]
    selection = {"expected_revision": current["revision"], "selection": state.get("selection", {"kind": "all"})}
    render_plan = client.plan(state["project_id"], selection)
    state["render"] = dict(selection, plan_sha256=render_plan["plan_sha256"])
    atomic_json(receipt, state)


def produce(args):
    project = args.project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    clips = project / "clips"
    clips.mkdir(exist_ok=True)
    film = project / "film.mp4"
    if film.is_file():
        progress(project, "done")
        return film
    client = RenderClient()
    receipt = project / "production.json"
    plan_file = project / "shots.json"
    revision_file = project / "revision.json"
    is_revision = revision_file.is_file()
    wait_for_models(client, project, args.profile,
                    needs_qwen=not receipt.exists() and (is_revision or not plan_file.exists()))
    if receipt.exists():
        state = read_json(receipt)
    else:
        story = args.story.read_text(encoding="utf-8-sig").strip()
        if not story:
            raise ValueError("Voer een verhaal of shotidee in.")
        pictures = [(f"image{index}", path) for index, path in enumerate(args.references, 17)]
        instructions = project / "writer-prompt.txt"
        writer_prompt = instructions.read_text(encoding="utf-8") if instructions.is_file() else DEFAULT_WRITER_PROMPT
        if is_revision:
            state = prepare_revision(project, read_json(revision_file), pictures, writer_prompt)
        else:
            if plan_file.exists():
                plan = read_json(plan_file)
            else:
                plan = plan_and_write(story, pictures, project, writer_prompt=writer_prompt)
                atomic_json(plan_file, plan)
            body = timeline_document(plan, project.name, args.profile, getattr(args, "aspect", None))
            for box, shot in zip(body["timeline"]["boxes"], plan["shots"]):
                box["prompt"] = shot["prompt"]
            progress(project, "references")
            for slot, path in pictures:
                asset = client.upload(path)
                body["assets"].append({"asset_id": asset["id"], "bindings": [
                    {"kind": "picture", "slot": slot, "title": path.stem},
                ]})
            state = {"plan": plan, "document": body, "key": uuid.uuid4().hex}
        atomic_json(receipt, state)
    atomic_json(project / "shots.json", state["plan"])
    atomic_json(project / "timeline.json", state["document"])
    progress(project, "rendering", completed=0, total=len(state["plan"]["shots"]))
    if "job_id" not in state:
        if "render" not in state:
            prepare_render(client, state, receipt)
        # The local Qwen server releases its GPU allocation after ten idle seconds.
        time.sleep(12)
        job = client.render(state["project_id"], state["render"], state["key"])
        state["job_id"] = job["id"]
        atomic_json(receipt, state)
    elif getattr(args, "resume", False):
        if client.job(state["job_id"]).get("state") in ("failed", "needs_reconciliation"):
            client.resume(state["job_id"])

    def update(job):
        if job.get("state") in ("queued", "running"):
            for attempt in job.get("attempts", []):
                unit = attempt["unit_id"]
                artifact = attempt.get("artifact")
                if (attempt["state"] != "verified" or not re.fullmatch(r"box:shot-\d{2}", unit)
                        or not artifact or artifact.get("unit_id") != unit):
                    continue
                box = unit.split(":")[1]
                target = clips / f"{box}.mp4"
                if target.is_file():
                    continue
                try:
                    client.download(artifact, target)
                except (OSError, RenderError) as error:
                    print(f"Preview voor {box} tijdelijk niet beschikbaar: {error}", flush=True)
        current = (job.get("progress") or {}).get("current") or ""
        match = re.fullmatch(r"box:shot-(\d{2})", current)
        finished = len(list(clips.glob("shot-*.mp4")))
        progress(project, "rendering", completed=finished, total=len(state["plan"]["shots"]),
                 current_shot=int(match[1]) if match else None)

    job = client.wait(state["job_id"], on_update=update)
    artifacts = {item["unit_id"]: item for item in job["artifacts"] if item["kind"] == "box"}
    progress(project, "exporting")
    for index in range(1, len(state["plan"]["shots"]) + 1):
        shot = f"shot-{index:02d}"
        client.download(artifacts[f"box:{shot}"], clips / f"{shot}.mp4")
    export_project(project, film)
    progress(project, "done")
    return film


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story", type=Path)
    parser.add_argument("project", type=Path)
    parser.add_argument("references", type=Path, nargs="+")
    parser.add_argument("--profile", default="studio-turbo8")
    parser.add_argument("--aspect", choices=ASPECT_SIZES)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if len(args.references) > 9:
        parser.error("Supply at most nine reference pictures")
    if any(mimetypes.guess_type(path.name)[0] not in {"image/jpeg", "image/png", "image/webp"}
           for path in args.references):
        parser.error("Reference pictures must be JPEG, PNG or WebP")
    with gpu_lock():
        try:
            print(produce(args))
        except Exception as error:
            progress(args.project, "failed", error=str(error))
            raise


if __name__ == "__main__":
    main()
