"""Produce the planned shots and a complete film on the Studio GPU host."""
import argparse
import mimetypes
import re
import subprocess
import time
import uuid
from pathlib import Path

from export_project import export_project
from prepare_timeline import ASPECT_SIZES, timeline_document
from studio_refine import review_before_next_shot, review_transitions
from studio_render import RenderClient, RenderError
from studio_storage import ProductionCancelled, atomic_json, check_cancelled, gpu_lock, progress, read_json
from studio_writer import DEFAULT_WRITER_PROMPT, plan_and_write, qwen_ready, read_reference_notes, revise_shot


def wait_for_models(client, project, profile_id, *, needs_qwen, can_cancel=True):
    progress(project, "preparing")
    until = time.monotonic() + 90 * 60
    while True:
        if can_cancel:
            check_cancelled(project)
        try:
            profiles = client.capabilities()["profiles"]
            profile = next((item for item in profiles if item["id"] == profile_id), {})
            if profile.get("ready") and (not needs_qwen or qwen_ready()):
                return
        except (OSError, ValueError, RenderError):
            pass
        if time.monotonic() >= until:
            raise RuntimeError("The video models are not available yet. Check the GPU host and resume the project.")
        time.sleep(5)


def prepare_revision(project, revision, pictures, writer_prompt):
    parent = read_json(project.parent / revision["parent"] / "production.json")
    plan, document = parent["plan"], parent["document"]
    document.setdefault("consumer", {})["studio_exports_clips"] = True
    if "prompts" in revision:
        prompts = {int(index): prompt for index, prompt in revision["prompts"].items()}
    else:
        index = revision["shot"]
        if "prompt" not in revision:
            progress(project, "writing", total=len(plan["shots"]))
            revise_shot(plan, index, revision["feedback"], pictures, writer_prompt,
                        reference_notes=read_reference_notes(project))
        prompts = {index: revision.get("prompt", plan["shots"][index]["prompt"])}
    ids = []
    for index, prompt in sorted(prompts.items()):
        plan["shots"][index]["prompt"] = prompt
        box = document["timeline"]["boxes"][index]
        box["prompt"] = prompt
        ids.append(box["id"])
    return {"plan": plan, "document": document, "project_id": parent["project_id"],
            "selection": {"kind": "boxes", "ids": ids, "rerender": True},
            "key": uuid.uuid4().hex}


def prepare_render(client, state, receipt, document=None, *, completed=0):
    if document is None:
        document = state["document"]
    if "selection" in state or completed:
        current = client.project(state["project_id"])
        expected = state.setdefault("base_revision", current["revision"])
        atomic_json(receipt, state)
        if current["revision"] == expected:
            current = client.update_project(state["project_id"], document, expected)
        elif current["revision"] != expected + 1 or current["timeline"] != document["timeline"]:
            raise RenderError("The render service has processed a different revision.")
    else:
        current = client.create(document, state["key"])
        state["project_id"] = current["id"]
    selection = {"expected_revision": current["revision"], "selection": state.get("selection", {"kind": "all"})}
    render_plan = client.plan(state["project_id"], selection)
    if completed:
        actions = {unit["id"]: unit["action"] for unit in render_plan["units"]}
        if any(actions.get("box:" + box["id"]) != "reuse"
               for box in document["timeline"]["boxes"][:completed]):
            raise RenderError("The render service could not reuse a previously reviewed shot.")
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
    revision = read_json(revision_file) if revision_file.is_file() else None
    state = read_json(receipt) if receipt.exists() else {}
    submitted = "job_id" in state or state.get("submitting", False)
    wait_for_models(client, project, args.profile,
                    needs_qwen=not state and (
                        not ("prompt" in revision or "prompts" in revision) if revision else not plan_file.exists()),
                    can_cancel=not submitted)
    if receipt.exists():
        if not submitted:
            check_cancelled(project)
    else:
        check_cancelled(project)
        story = args.story.read_text(encoding="utf-8-sig").strip()
        if not story:
            raise ValueError("Enter a story or shot idea.")
        pictures = [(f"image{index}", path) for index, path in enumerate(args.references, 17)]
        instructions = project / "writer-prompt.txt"
        writer_prompt = instructions.read_text(encoding="utf-8") if instructions.is_file() else DEFAULT_WRITER_PROMPT
        if revision:
            state = prepare_revision(project, revision, pictures, writer_prompt)
        else:
            if plan_file.exists():
                plan = read_json(plan_file)
            else:
                plan = plan_and_write(story, pictures, project, writer_prompt=writer_prompt)
                atomic_json(plan_file, plan)
            check_cancelled(project)
            body = timeline_document(plan, project.name, args.profile, getattr(args, "aspect", None))
            for box, shot in zip(body["timeline"]["boxes"], plan["shots"]):
                box["prompt"] = shot["prompt"]
            progress(project, "references")
            for slot, path in pictures:
                check_cancelled(project)
                asset = client.upload(path)
                body["assets"].append({"asset_id": asset["id"], "bindings": [
                    {"kind": "picture", "slot": slot, "title": path.stem},
                ]})
            state = {"plan": plan, "document": body, "key": uuid.uuid4().hex,
                     "completed_shots": 0, "handoffs": {}}
        atomic_json(receipt, state)
    atomic_json(project / "shots.json", state["plan"])
    atomic_json(project / "timeline.json", state["document"])
    if "completed_shots" in state:
        render_with_reviews(client, args, project, state, receipt, clips)
    else:
        artifacts = render_shots(client, args, project, state, receipt, clips, state["document"])
        progress(project, "exporting")
        for box in state["document"]["timeline"]["boxes"]:
            check_cancelled(project)
            shot = box["id"]
            client.download(artifacts[f"box:{shot}"], clips / f"{shot}.mp4")
    check_cancelled(project)
    review_transitions(project, state["plan"]["shots"], story=state["plan"].get("story", ""))
    check_cancelled(project)
    progress(project, "exporting")
    export_project(project, film)
    progress(project, "done")
    return film


def render_with_reviews(client, args, project, state, receipt, clips):
    plan, document = state["plan"], state["document"]
    boxes = document["timeline"]["boxes"]
    pictures = [(f"image{index}", path) for index, path in enumerate(args.references, 17)]
    instructions = project / "writer-prompt.txt"
    writer_prompt = instructions.read_text(encoding="utf-8") if instructions.is_file() else DEFAULT_WRITER_PROMPT
    reference_notes = read_reference_notes(project)
    for index in range(state["completed_shots"], len(boxes)):
        if "job_id" not in state and not state.get("submitting"):
            check_cancelled(project)
        if index and str(index) not in state["handoffs"]:
            progress(project, "adapting", completed=index, total=len(boxes), current_shot=index + 1)
            try:
                review = review_before_next_shot(
                    plan, index, clips / f"{boxes[index - 1]['id']}.mp4",
                    pictures, writer_prompt, reference_notes,
                )
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                raise RuntimeError(
                    f"Could not review shot {index} before rendering shot {index + 1}. "
                    f"Resume the project to retry this review. {error}"
                ) from error
            check_cancelled(project)
            if review["prompt"]:
                plan["shots"][index]["prompt"] = boxes[index]["prompt"] = review["prompt"]
            state["handoffs"][str(index)] = {
                "observations": review["observations"], "adjusted": bool(review["prompt"]),
            }
            atomic_json(receipt, state)
            atomic_json(project / "shots.json", plan)
            atomic_json(project / "timeline.json", document)
        prefix = {**document, "timeline": {**document["timeline"], "boxes": boxes[:index + 1]}}
        artifacts = render_shots(client, args, project, state, receipt, clips, prefix, completed=index)
        check_cancelled(project)
        shot = boxes[index]["id"]
        client.download(artifacts[f"box:{shot}"], clips / f"{shot}.mp4")
        for key in ("render", "base_revision", "submitting", "job_id"):
            state.pop(key, None)
        state.update(completed_shots=index + 1, key=uuid.uuid4().hex)
        atomic_json(receipt, state)


def render_shots(client, args, project, state, receipt, clips, document, *, completed=0):
    total = len(state["plan"]["shots"])
    progress(project, "rendering", completed=completed, total=total)
    if "job_id" not in state:
        if "render" not in state:
            prepare_render(client, state, receipt, document, completed=completed)
        if not state.get("submitting"):
            # The local Qwen server releases its GPU allocation after ten idle seconds.
            time.sleep(12)
            check_cancelled(project)
            state["submitting"] = True
            atomic_json(receipt, state)
        job = client.render(state["project_id"], state["render"], state["key"])
        state["job_id"] = job["id"]
        atomic_json(receipt, state)
    elif getattr(args, "resume", False) and not (project / "cancel.request").is_file():
        if client.job(state["job_id"]).get("state") in ("failed", "needs_reconciliation"):
            client.resume(state["job_id"])

    cancel_sent = False

    def update(job):
        nonlocal cancel_sent
        if (project / "cancel.request").is_file():
            if job.get("state") in ("succeeded", "failed"):
                raise ProductionCancelled()
            if not cancel_sent:
                client.cancel(state["job_id"])
                cancel_sent = True
            progress(project, "cancelling")
            return True
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
                    print(f"Preview for {box} is temporarily unavailable: {error}", flush=True)
        current = (job.get("progress") or {}).get("current") or ""
        match = re.fullmatch(r"box:shot-(\d{2})", current)
        finished = len(list(clips.glob("shot-*.mp4")))
        progress(project, "rendering", completed=finished, total=total,
                 current_shot=int(match[1]) if match else None)

    job = client.wait(state["job_id"], on_update=update)
    return {item["unit_id"]: item for item in job["artifacts"] if item["kind"] == "box"}


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
        except ProductionCancelled:
            progress(args.project, "cancelled")
        except Exception as error:
            progress(args.project, "failed", error=str(error))
            raise


if __name__ == "__main__":
    main()
