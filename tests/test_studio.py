"""Regression checks for production retries and the browser service boundary."""
import base64
from io import BytesIO
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

import run_studio
from studio_refine import review_timestamps
import studio_refine
from studio_render import RenderClient, RenderError
from studio_storage import read_json
from prepare_timeline import timeline_document
import studio_web as web
import studio_writer as writer


def image_data():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def shot_plan(count=5):
    return {"shots": [{"action": "A boat drifts", "camera": "Wide shot", "sound": "Water",
                       "start": "Boat left", "end": "Boat right", "duration_seconds": 3} for _ in range(count)]}


class WriterTests(unittest.TestCase):
    def test_handoff_uses_original_character_reference_and_exact_last_frame(self):
        plan = shot_plan(2)
        plan["story"] = "Eva opens the door and walks outside."
        for shot in plan["shots"]:
            shot["prompt"] = "Eva from @image17 opens the door."
        pictures = [("image17", Path("reference-01.png"))]
        notes = {"reference-01.png": "Eva has short dark hair."}
        with (
            patch.object(studio_refine, "clip_duration", return_value=3),
            patch.object(studio_refine, "extract_review_frame", return_value=True) as extract,
            patch.object(studio_refine, "ask_qwen", return_value={
                "observations": "Eva has opened the door.",
                "prompt": "Eva steps through the open door.",
            }) as ask,
        ):
            review = studio_refine.review_before_next_shot(
                plan, 1, Path("shot-01.mp4"), pictures, writer.DEFAULT_WRITER_PROMPT, notes,
            )
        self.assertEqual(ask.call_args.args[2], pictures)
        self.assertEqual(ask.call_args.kwargs["reference_notes"], notes)
        self.assertEqual(json.loads(ask.call_args.args[1])["story"], plan["story"])
        self.assertTrue(extract.call_args.kwargs["last"])
        self.assertIn("exact last video frame", ask.call_args.kwargs["clip_frames"][-1][2])
        self.assertIn("@image17", review["prompt"])

    def test_reference_notes_are_attached_to_the_matching_qwen_image(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = (Path(folder) / name for name in ("reference-01.png", "reference-02.png"))
            for path in (first, second):
                path.write_bytes(base64.b64decode(image_data()))
            response = BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
            with patch.dict(writer.os.environ, {"LLAMA_API_KEY": "test"}), \
                    patch.object(writer, "urlopen", return_value=response) as call:
                writer.ask_qwen("system", "story", [("image17", first), ("image18", second)],
                               reference_notes={second.name: "Eva is on the left. Use this for appearance."})
            content = json.loads(call.call_args.args[0].data)["messages"][1]["content"]
            self.assertEqual(content[1]["text"], "Reference @image17")
            self.assertIn("Reference @image18\n", content[3]["text"])
            self.assertIn("Eva is on the left.", content[3]["text"])
            self.assertEqual(content[4]["type"], "image_url")

    def test_review_frame_count_scales_with_clip_length(self):
        for duration, count in ((0.5, 3), (1, 3), (3, 6), (5, 9), (10, 15)):
            with self.subTest(duration=duration):
                timestamps = review_timestamps(duration)
                self.assertEqual(len(timestamps), count)
                self.assertEqual(timestamps, sorted(set(timestamps)))
                self.assertGreater(timestamps[0], 0)
                self.assertLess(timestamps[-1], duration)
                self.assertAlmostEqual(timestamps[0] + timestamps[-1], duration)

    def test_writer_restores_references_omitted_from_individual_shots(self):
        for pictures, expected in (
            ([("image17", None)], ("@image17", "@image17")),
            ([("image17", None), ("image18", None)], ("@image17", "@image18")),
        ):
            with self.subTest(pictures=pictures):
                plan = shot_plan(2)
                if len(pictures) > 1:
                    plan["shots"][0]["reference_slots"] = ["image17"]
                    plan["shots"][1]["reference_slots"] = ["@image18"]
                with patch.object(writer, "ask_qwen", side_effect=[
                    plan, {"prompts": ["@image17 enters.", "The subject turns."]},
                ]):
                    result = writer.plan_and_write("Two shots", pictures)
                for shot, reference in zip(result["shots"], expected):
                    self.assertIn(reference, shot["prompt"])

    def test_invalid_reference_slots_are_replanned_before_writing(self):
        pictures = [("image17", None), ("image18", None)]
        invalid = shot_plan(1)
        invalid["shots"][0]["reference_slots"] = ["image2"]
        for resume in (False, True):
            with self.subTest(resume=resume), tempfile.TemporaryDirectory() as folder:
                project = Path(folder)
                saved = project / "plan.json"
                if resume:
                    saved.write_text(json.dumps(invalid), encoding="utf-8")
                corrected = shot_plan(1)
                corrected["shots"][0]["reference_slots"] = ["image18"]
                replies = [corrected, {"prompts": ["The boat drifts."]}]
                if not resume:
                    replies.insert(0, invalid)
                with patch.object(writer, "ask_qwen", side_effect=replies) as call:
                    result = writer.plan_and_write("A boat", pictures, project)
                self.assertEqual(call.call_count, len(replies))
                self.assertIn("image17, image18", call.call_args_list[-2].args[0])
                self.assertEqual(read_json(saved)["shots"][0]["reference_slots"], ["image18"])
                self.assertIn("@image18", result["shots"][0]["prompt"])
                self.assertNotIn("@image17", result["shots"][0]["prompt"])

    def test_qwen_retries_truncated_output_without_thinking(self):
        def response(finish_reason, content):
            return BytesIO(json.dumps({"choices": [{
                "finish_reason": finish_reason, "message": {"content": content},
            }]}).encode())

        with (
            patch.dict(writer.os.environ, {"LLAMA_API_KEY": "test"}),
            patch.object(writer, "urlopen", side_effect=[
                response("length", '{"shots":['), response("stop", '{"shots":[]}'),
            ]) as calls,
        ):
            self.assertEqual(writer.ask_qwen("system", "user", []), {"shots": []})
        payloads = [json.loads(call.args[0].data) for call in calls.call_args_list]
        self.assertEqual([body["max_tokens"] for body in payloads], [8192, 16384])
        self.assertTrue(all(body["chat_template_kwargs"] == {"enable_thinking": False} for body in payloads))

    def test_custom_instructions_replace_the_default_only_for_the_writer(self):
        instructions = "Describe when the boat slows and when it stops."
        with patch.object(writer, "ask_qwen", side_effect=[shot_plan(1), {"prompts": ["@image17 boat"]}]) as call:
            writer.plan_and_write("A boat", [("image17", None)], writer_prompt=instructions)
        self.assertNotIn(instructions, call.call_args_list[0].args[0])
        self.assertTrue(call.call_args_list[1].args[0].endswith(instructions))
        self.assertIn("exactly 1 nonempty strings", call.call_args_list[1].args[0])

    def test_simple_and_complex_ideas_keep_the_planners_count(self):
        for count in (1, 7):
            with self.subTest(count=count):
                plan = shot_plan(count)
                prompts = {"prompts": ["@image17 boat"] * count}
                with patch.object(writer, "ask_qwen", side_effect=[plan, prompts]) as call:
                    result = writer.plan_and_write("A boat", [("image17", None)])
                self.assertEqual(len(result["shots"]), count)
                self.assertIn(f"exactly {count} nonempty strings", call.call_args.args[0])

    def test_rejects_duration_over_ten_seconds(self):
        plan = shot_plan(1)
        plan["shots"][0]["duration_seconds"] = 11
        with patch.object(writer, "ask_qwen", return_value=plan):
            with self.assertRaisesRegex(ValueError, "duration from 1 to 10"):
                writer.plan_and_write("A boat", [("image17", None)])

    def test_writer_receives_visible_handoffs_at_continuous_boundaries(self):
        plan = shot_plan(3)
        plan["shots"][0]["end"] = "Eva stands next to the open door."
        plan["shots"][1]["start"] = "Eva starts stepping through the open door."
        plan["shots"][2]["cut"] = True
        prompts = {"prompts": ["@image17 Eva"] * 3}
        with patch.object(writer, "ask_qwen", side_effect=[plan, prompts]) as call:
            writer.plan_and_write("Eva opens the door and walks through it.", [("image17", None)])
        first, second, third = json.loads(call.call_args.args[1])["shots"]
        self.assertEqual(first["next_opening_state"], second["start"])
        self.assertEqual(second["previous_closing_state"], first["end"])
        self.assertNotIn("next_opening_state", second)
        self.assertNotIn("previous_closing_state", third)

    def test_writer_failure_reuses_saved_plan_and_rejects_unknown_references(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            with patch.object(writer, "ask_qwen", side_effect=[shot_plan(), OSError("offline")]):
                with self.assertRaises(OSError):
                    writer.plan_and_write("A boat", [("image17", None)], project)
            self.assertTrue((project / "plan.json").is_file())
            with patch.object(writer, "ask_qwen", return_value={"prompts": ["@image99 boat"] * 5}) as call:
                with self.assertRaisesRegex(ValueError, "unknown picture slot"):
                    writer.plan_and_write("A boat", [("image17", None)], project)
                self.assertEqual(call.call_count, 1)
            with patch.object(writer, "ask_qwen", return_value={"prompts": ["@image17 boat"] * 5}):
                result = writer.plan_and_write("A boat", [("image17", None)], project)
                self.assertTrue(all(shot["prompt"] for shot in result["shots"]))


class TimelineTests(unittest.TestCase):
    def test_native_frame_lengths_are_variable_and_capped(self):
        plan = shot_plan(4)
        plan["shots"][0]["duration_seconds"] = 10
        plan["shots"][1]["duration_seconds"] = 1
        plan["shots"][2]["duration_seconds"] = 10
        plan["shots"][3].update(duration_seconds=10, cut=True)
        boxes = timeline_document(plan, "test", "studio-turbo8")["timeline"]["boxes"]
        self.assertEqual([box["frames"] for box in boxes], [226, 17, 238, 226])
        self.assertTrue(boxes[-1]["cut"])

    def test_short_opening_can_continue_into_next_shot(self):
        plan = shot_plan(2)
        plan["shots"][0]["duration_seconds"] = 1
        document = timeline_document(plan, "test", "studio-turbo8")
        self.assertEqual(document["timeline"]["boxes"][0]["frames"], 22)
        self.assertEqual(document["settings"]["context_frames"], 22)

    def test_previous_plans_keep_their_default_timing(self):
        plan = shot_plan()
        for shot in plan["shots"]:
            del shot["duration_seconds"]
        boxes = timeline_document(plan, "test", "studio-turbo8")["timeline"]["boxes"]
        self.assertEqual([box["frames"] for box in boxes], [90, 68, 68, 68, 68])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = patch.object(web, "ROOT", Path(self.folder.name))
        self.root.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.StudioHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.root.stop()
        self.folder.cleanup()

    def post(self, path, payload, origin=None):
        return urlopen(Request(self.url + path, data=json.dumps(payload).encode(), headers={
            "Origin": origin or self.url, "Content-Type": "application/json"}))

    def test_upload_status_files_and_video_range(self):
        with patch.object(web, "schedule") as schedule:
            response = self.post("/projects", {"story": "A boat", "images": [image_data()]})
            self.assertEqual(response.status, 202)
            identifier = json.load(response)["id"]
            schedule.assert_called_once()
        project = web.ROOT / identifier
        (project / "film.mp4").write_bytes(b"0123456789")
        status = json.load(urlopen(self.url + "/projects/" + identifier))
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["story"], "A boat")
        self.assertEqual(status["references"], ["reference-01.png"])
        response = urlopen(Request(self.url + "/projects/" + identifier + "/film.mp4", headers={"Range": "bytes=3-6"}))
        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers["Content-Range"], "bytes 3-6/10")
        self.assertEqual(response.read(), b"3456")
        self.assertEqual(len(json.load(urlopen(self.url + "/projects"))["projects"]), 1)
        with self.assertRaises(HTTPError) as error:
            self.post("/projects/" + identifier + "/resume", {})
        self.assertEqual(error.exception.code, 409)
        error.exception.close()

    def test_cut_preview_uses_rendered_durations(self):
        project = web.stage({"story": "A boat", "images": [image_data()]})
        web.atomic_json(project / "shots.json", shot_plan(3))
        (project / "film.mp4").touch()
        with patch.object(web, "clip_duration", side_effect=[3.25, 1.5, 5]) as duration:
            with urlopen(f"{self.url}/projects/{project.name}/cuts/2") as response:
                self.assertEqual(json.load(response), {"start": 3.25, "end": 6.75})
        self.assertEqual([call.args[0].name for call in duration.call_args_list],
                         ["shot-01.mp4", "shot-02.mp4", "shot-03.mp4"])

    def test_rejects_cross_origin_and_corrupt_images_without_leaving_project(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/projects", {}, origin="http://example.com")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.post("/projects", {"story": "A boat", "images": [base64.b64encode(b"not a picture").decode()]})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(list(web.ROOT.iterdir()), [])

    def test_retry_cannot_enqueue_same_project_twice(self):
        project = web.stage({"story": "A boat", "images": [image_data()]})
        web.set_status(project, "failed")
        with patch.object(web.QUEUE, "submit") as submit:
            try:
                self.assertTrue(web.schedule(project, resume=True))
                self.assertFalse(web.schedule(project, resume=True))
                submit.assert_called_once_with(web.render, project, True)
            finally:
                web.ACTIVE.discard(project.name)

    def test_stop_and_clear_projects_through_http(self):
        active = web.stage({"story": "A boat", "images": [image_data()]})
        finished = web.stage({"story": "A train", "images": [image_data()]})
        web.set_status(finished, "done")
        other = web.ROOT / "unmanaged"
        other.mkdir()
        (other / "status").write_text("done")
        with self.assertRaises(HTTPError) as error:
            self.post("/projects/clear", {})
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        with self.post("/projects/cancel", {}) as response:
            self.assertEqual(json.load(response), {"requested": 1})
        self.assertTrue((active / "cancel.request").exists())
        self.assertFalse((finished / "cancel.request").exists())
        self.assertEqual(web.project_data(active)["progress"]["stage"], "cancelling")
        web.set_status(active, "cancelled")
        with self.post("/projects/clear", {}) as response:
            self.assertEqual(json.load(response), {"removed": 2})
        self.assertTrue(other.is_dir())
        self.assertFalse(active.exists())
        self.assertFalse(finished.exists())

    def test_refinement_drafts_without_rendering_and_uses_the_approved_prompt(self):
        parent = web.stage({"story": "A boat", "images": [image_data()], "image_notes": ["Use this for the boat."]})
        self.assertEqual(web.project_data(parent, details=True)["image_notes"],
                         {"reference-01.png": "Use this for the boat."})
        (parent / "film.mp4").write_bytes(b"original")
        plan = {**shot_plan(1), "story": "A boat"}
        plan["shots"][0]["prompt"] = "@image17 boat moves."
        document = timeline_document(plan, parent.name, "studio-turbo8")
        web.atomic_json(parent / "production.json", {
            "plan": plan, "document": document, "project_id": "native-project",
        })
        with patch.object(web.QUEUE, "submit") as submit, patch.object(web, "schedule") as schedule:
            with self.post(f"/projects/{parent.name}/refinements", {
                "shot": 0, "feedback": "Make the boat slow down.",
            }) as response:
                self.assertEqual(response.status, 202)
                draft = json.load(response)
            schedule.assert_not_called()
            task, project, directory = submit.call_args.args
            result = {"prompt": "@image17 boat slows.", "observations": "The boat maintains its speed.", "frames": []}
            with patch.object(web, "refine_clip", return_value=result), patch.object(web, "gpu_lock"):
                task(project, directory)
            with urlopen(self.url + f"/projects/{parent.name}/refinements/{draft['id']}") as response:
                self.assertEqual(json.load(response)["prompt"], result["prompt"])
            approved = "@image17 boat slows and settles near the dock."
            with self.post(f"/projects/{parent.name}/revisions", {
                "shot": 0, "refinement_id": draft["id"], "prompt": approved,
            }) as response:
                child = web.ROOT / json.load(response)["id"]
            schedule.assert_called_once_with(child)
            self.assertEqual(writer.read_reference_notes(child), writer.read_reference_notes(parent))
            with patch.object(run_studio, "revise_shot") as writer_call:
                state = run_studio.prepare_revision(child, read_json(child / "revision.json"), [], "")
                writer_call.assert_not_called()
            self.assertEqual(state["document"]["timeline"]["boxes"][0]["prompt"], approved)
            self.assertEqual((parent / "film.mp4").read_bytes(), b"original")

    def test_whole_film_review_passes_ordered_samples_and_saves_findings(self):
        project = web.stage({"story": "A boat", "images": [image_data()]})
        (project / "film.mp4").write_bytes(b"original")
        plan = {**shot_plan(3), "story": "A boat"}
        for shot in plan["shots"]:
            shot["prompt"] = "@image17 boat."
        web.atomic_json(project / "production.json", {
            "plan": plan, "document": timeline_document(plan, project.name, "studio-turbo8"),
            "project_id": "native-project",
        })
        with patch.object(web.QUEUE, "submit") as submit:
            with self.post(f"/projects/{project.name}/film-review", {"feedback": "Check story flow."}) as response:
                self.assertEqual(response.status, 202)
            task, root, directory = submit.call_args.args
        with (
            patch.object(web, "gpu_lock"),
            patch.object(studio_refine, "clip_duration", return_value=3),
            patch.object(studio_refine, "extract_review_frame", return_value=True),
            patch.object(studio_refine, "ask_qwen", return_value={
                "summary": "The second shot repeats the opening.",
                "reviews": [
                    {"shot": 1, "observations": "Position matches."},
                    {"shot": 2, "observations": "Action restarts.", "feedback": "Continue from the dock.",
                     "prompt": "@image17 boat continues from the dock."},
                    {"shot": 3, "observations": "The boat jumps.", "feedback": "Keep the boat moving forward.",
                     "prompt": "@image17 boat continues moving forward."},
                ],
            }) as review,
        ):
            task(root, directory)
        self.assertEqual(json.loads(review.call_args.args[1])["feedback"], "Check story flow.")
        self.assertEqual([label for _, _, label in review.call_args.kwargs["clip_frames"]],
                         [f"Shot {shot}: {sample} video frame" for shot in (1, 2, 3)
                          for sample in ("first", "middle", "last")])
        with urlopen(self.url + f"/projects/{project.name}") as response:
            result = json.load(response)["film_review"]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reviews"]["1"], {"observations": "Position matches."})
        with patch.object(web, "schedule"):
            with self.post(f"/projects/{project.name}/film-revision", {"review_id": result["id"]}) as response:
                child = web.ROOT / json.load(response)["id"]
        revised = run_studio.prepare_revision(child, read_json(child / "revision.json"), [], "")
        self.assertEqual(revised["selection"]["ids"], ["shot-02", "shot-03"])
        self.assertEqual(revised["plan"]["shots"][0]["prompt"], plan["shots"][0]["prompt"])
        self.assertEqual(revised["plan"]["shots"][2]["prompt"], "@image17 boat continues moving forward.")
        self.assertEqual((project / "film.mp4").read_bytes(), b"original")

    def test_single_refinement_includes_the_neighbor_boundaries(self):
        project = web.stage({"story": "A boat", "images": [image_data()]})
        (project / "film.mp4").write_bytes(b"original")
        web.atomic_json(project / "production.json", {"plan": {**shot_plan(3), "story": "A boat"}})
        clips = project / "clips"
        clips.mkdir()
        for index in range(1, 4):
            (clips / f"shot-{index:02d}.mp4").touch()
        with (
            patch.object(studio_refine, "clip_evidence", return_value=[(0.2, project / "frame-1.jpg")]),
            patch.object(studio_refine, "extract_review_frame", return_value=True) as extract,
            patch.object(studio_refine, "suggest_revision", return_value={"prompt": "Boat slows."}) as suggest,
        ):
            review = studio_refine.refine_clip(project, project, {"shot": 1, "feedback": "Keep moving."})
        self.assertEqual([call.args[0].name for call in extract.call_args_list], ["shot-01.mp4", "shot-03.mp4"])
        self.assertEqual([call.kwargs["last"] for call in extract.call_args_list], [True, False])
        self.assertEqual([entry[1].name for entry in suggest.call_args.kwargs["clip_frames"]],
                         ["previous-last.jpg", "frame-1.jpg", "next-first.jpg"])
        self.assertEqual(len(review["neighbor_frames"]), 2)

    def test_runpod_proxy_requires_login_and_its_own_https_origin(self):
        self.server.public_origin = "https://testpod-8190.proxy.runpod.net"
        self.server.studio_auth = b"Basic " + base64.b64encode(b"studio:local-test-password")
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/projects")
        self.assertEqual(error.exception.code, 401)
        self.assertIn('H3 Video Studio', error.exception.headers["WWW-Authenticate"])
        error.exception.close()
        with urlopen(self.url + "/health") as response:
            self.assertEqual(response.status, 200)
        headers = {"Authorization": self.server.studio_auth.decode(),
                   "Content-Type": "application/json", "Origin": "https://other.example"}
        payload = json.dumps({"story": "A boat", "images": [image_data()]}).encode()
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + "/projects", data=payload, headers=headers))
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        headers["Origin"] = self.server.public_origin
        with patch.object(web, "schedule"), urlopen(Request(self.url + "/projects", data=payload, headers=headers)) as response:
            self.assertEqual(response.status, 202)
        self.server.studio_auth = None
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + "/projects", headers=headers))
        self.assertEqual(error.exception.code, 503)
        error.exception.close()


class ProductionTests(unittest.TestCase):
    def test_stop_sends_native_cancel_and_waits_for_terminal_state(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = {"job_id": "job", "plan": {"shots": []}, "document": {}}
            web.atomic_json(project / "production.json", state)
            (project / "cancel.request").touch()
            args = SimpleNamespace(project=project, profile="studio-turbo8")
            client = unittest.mock.MagicMock()

            def wait_for_cancel(job_id, on_update):
                self.assertTrue(on_update({"state": "running"}))
                self.assertTrue(on_update({"state": "needs_reconciliation"}))
                on_update({"state": "failed"})

            client.wait.side_effect = wait_for_cancel
            with (
                patch.object(run_studio, "RenderClient", return_value=client),
                patch.object(run_studio, "wait_for_models"),
                self.assertRaises(run_studio.ProductionCancelled),
            ):
                run_studio.produce(args)
            client.cancel.assert_called_once_with("job")
            client.render.assert_not_called()

    def test_project_writer_prompt_reaches_the_production_worker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(web, "ROOT", Path(folder)):
            instructions = "Keep the camera locked and describe each movement."
            project = web.stage({"story": "A boat", "images": [image_data()], "writer_prompt": instructions})
            image = project / "reference-01.png"
            args = SimpleNamespace(project=project, story=project / "story.txt",
                                   references=[image], profile="studio-turbo8")
            with (
                patch.object(run_studio, "wait_for_models"),
                patch.object(run_studio, "plan_and_write", side_effect=OSError("offline")) as planner,
                self.assertRaisesRegex(OSError, "offline"),
            ):
                run_studio.produce(args)
            planner.assert_called_once_with("A boat", [("image17", image)], project,
                                            writer_prompt=instructions)

    def test_shot_revision_preserves_previous_version_and_uses_native_selection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(web, "ROOT", Path(folder)):
            parent = web.stage({"story": "A boat", "images": [image_data()]})
            plan = {**shot_plan(2), "story": "A boat"}
            for shot in plan["shots"]:
                shot["prompt"] = "@image17 boat"
            document = timeline_document(plan, parent.name, "studio-turbo8")
            for box in document["timeline"]["boxes"]:
                box["prompt"] = "@image17 boat"
            original = {"plan": plan, "document": document, "project_id": "native-project"}
            web.atomic_json(parent / "production.json", original)
            (parent / "film.mp4").write_bytes(b"original")
            revision = web.stage_revision(parent, {"shot": 1, "feedback": "Have the boat slow down."})
            pictures = [("image17", revision / "reference-01.png")]
            with patch.object(writer, "ask_qwen", return_value={"prompt": "@image17 boat slows down"}):
                state = run_studio.prepare_revision(
                    revision, read_json(revision / "revision.json"), pictures, writer.DEFAULT_WRITER_PROMPT)
            self.assertEqual(state["document"]["timeline"]["boxes"][0]["prompt"], "@image17 boat")
            self.assertEqual(state["document"]["timeline"]["boxes"][1]["prompt"], "@image17 boat slows down")
            self.assertEqual(read_json(parent / "production.json"), original)
            self.assertEqual((parent / "film.mp4").read_bytes(), b"original")
            client = unittest.mock.MagicMock()
            client.project.return_value = {"revision": 3, "timeline": state["document"]["timeline"]}
            client.plan.return_value = {"plan_sha256": "hash"}
            state["base_revision"] = 2
            run_studio.prepare_render(client, state, revision / "production.json")
            client.update_project.assert_not_called()
            client.plan.assert_called_once_with("native-project", {
                "expected_revision": 3, "selection": {"kind": "boxes", "ids": ["shot-02"], "rerender": True},
            })

    def test_lost_render_response_reuses_request_and_saved_prompts(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            story = project / "story.txt"
            story.write_text("A boat")
            image = project / "reference-01.png"
            image.write_bytes(base64.b64decode(image_data()))
            plan = shot_plan(7)
            for shot in plan["shots"]:
                shot["prompt"] = "@image17 boat moves"
            client = unittest.mock.MagicMock()
            client.capabilities.return_value = {"profiles": [{"id": "studio-turbo8", "ready": True}]}
            client.upload.return_value = {"id": "asset"}
            client.create.return_value = {"id": "project", "revision": 1}
            client.render.side_effect = [OSError("response lost"), *[{"id": i} for i in range(1, 8)]]
            client.download.side_effect = lambda artifact, target: target.write_bytes(b"clip")
            artifacts = [{"kind": "box", "unit_id": f"box:shot-{i:02d}"} for i in range(1, 8)]

            def update_project(project_id, document, expected_revision):
                boxes = document["timeline"]["boxes"]
                self.assertEqual(len(boxes), expected_revision + 1)
                if len(boxes) == 2:
                    self.assertEqual(boxes[-1]["prompt"], "@image17 boat keeps moving")
                current = {"revision": expected_revision + 1, "timeline": document["timeline"]}
                client.project.return_value = current
                return current

            def render_plan(project_id, request):
                count = request["expected_revision"]
                return {"plan_sha256": "hash", "units": [
                    {"id": artifact["unit_id"], "action": "render" if i == count else "reuse"}
                    for i, artifact in enumerate(artifacts[:count], 1)
                ]}

            def wait_for_clips(job, on_update):
                on_update({"state": "queued", "attempts": [
                    {"state": "verified", "unit_id": artifacts[job - 1]["unit_id"],
                     "artifact": artifacts[job - 1]},
                ], "progress": {"current": f"box:shot-{job:02d}"}})
                self.assertTrue((project / "clips" / f"shot-{job:02d}.mp4").is_file())
                self.assertFalse((project / "clips" / f"shot-{job + 1:02d}.mp4").exists())
                return {"artifacts": artifacts[:job]}

            def review_next(saved_plan, index, previous_clip, pictures, writer_prompt, reference_notes):
                self.assertEqual(previous_clip.read_bytes(), b"clip")
                self.assertEqual(client.render.call_count, index + 1)
                self.assertEqual(pictures, [("image17", image)])
                return {"observations": "The boat already started moving.",
                        "prompt": "@image17 boat keeps moving" if index == 1 else ""}

            client.project.return_value = {"revision": 1}
            client.update_project.side_effect = update_project
            client.plan.side_effect = render_plan
            client.wait.side_effect = wait_for_clips
            args = SimpleNamespace(project=project, story=story, references=[image], profile="studio-turbo8", resume=False)
            def export(root, target):
                self.assertEqual(len(list((root / "clips").glob("*.mp4"))), 7)
                target.write_bytes(b"movie")
            with (
                patch.object(run_studio, "RenderClient", return_value=client),
                patch.object(run_studio, "qwen_ready", return_value=True),
                patch.object(run_studio, "plan_and_write", return_value=plan) as planner,
                patch.object(run_studio.time, "sleep"),
                patch.object(run_studio, "review_before_next_shot", side_effect=review_next) as handoff,
                patch.object(studio_refine, "review_transition", side_effect=[
                    OSError("Qwen temporarily unavailable"),
                    *[{"verdict": "pass", "observations": "Consistent", "suggestion": ""}] * 5,
                ]) as reviewer,
                patch.object(run_studio, "export_project", side_effect=export),
            ):
                with self.assertRaises(OSError):
                    run_studio.produce(args)
                saved_key = read_json(project / "production.json")["key"]
                self.assertEqual(run_studio.produce(args).read_bytes(), b"movie")
                self.assertEqual(handoff.call_count, 6)
                self.assertEqual(len(client.create.call_args.args[0]["timeline"]["boxes"]), 1)
                self.assertTrue(read_json(project / "production.json")["handoffs"]["1"]["adjusted"])
                self.assertEqual(reviewer.call_count, 6)
                self.assertEqual([path.name for path in reviewer.call_args_list[0].args[:2]],
                                 ["shot-01.mp4", "shot-02.mp4"])
                transitions = read_json(project / "transitions.json")
                self.assertEqual(transitions["1"]["verdict"], "unavailable")
                self.assertEqual(transitions["2"]["verdict"], "pass")
                planner.assert_called_once_with("A boat", [("image17", image)], project,
                                                writer_prompt=writer.DEFAULT_WRITER_PROMPT)
                client.create.assert_called_once()
                self.assertEqual(client.render.call_args_list[0], client.render.call_args_list[1])
                self.assertEqual(client.render.call_args_list[1].args[2], saved_key)
                # A completed project does not submit additional GPU work.
                self.assertEqual(run_studio.produce(args).read_bytes(), b"movie")
                self.assertEqual(client.render.call_count, 8)


class DownloadTests(unittest.TestCase):
    def test_corrupt_download_preserves_previous_file(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"corrupt")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "shot.mp4"
                target.write_bytes(b"previous")
                artifact = {"size": 7, "sha256": hashlib.sha256(b"correct").hexdigest(), "download_url": "/h3c/api/v1/artifacts/one/download"}
                client = RenderClient(f"http://127.0.0.1:{server.server_port}")
                with self.assertRaises(RenderError):
                    client.download(artifact, target)
                self.assertEqual(target.read_bytes(), b"previous")
                self.assertEqual(list(Path(folder).glob("*.tmp")), [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
