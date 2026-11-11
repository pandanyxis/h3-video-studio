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
    def test_custom_instructions_replace_the_default_only_for_the_writer(self):
        instructions = "Describe when the boat slows and when it stops."
        with patch.object(writer, "ask_qwen", side_effect=[shot_plan(1), {"prompts": ["@image17 boat"]}]) as call:
            writer.plan_and_write("Een boot", [("image17", None)], writer_prompt=instructions)
        self.assertNotIn(instructions, call.call_args_list[0].args[0])
        self.assertTrue(call.call_args_list[1].args[0].endswith(instructions))
        self.assertIn("exactly 1 nonempty strings", call.call_args_list[1].args[0])

    def test_simple_and_complex_ideas_keep_the_planners_count(self):
        for count in (1, 7):
            with self.subTest(count=count):
                plan = shot_plan(count)
                prompts = {"prompts": ["@image17 boat"] * count}
                with patch.object(writer, "ask_qwen", side_effect=[plan, prompts]) as call:
                    result = writer.plan_and_write("Een boot", [("image17", None)])
                self.assertEqual(len(result["shots"]), count)
                self.assertIn(f"exactly {count} nonempty strings", call.call_args.args[0])

    def test_rejects_duration_over_ten_seconds(self):
        plan = shot_plan(1)
        plan["shots"][0]["duration_seconds"] = 11
        with patch.object(writer, "ask_qwen", return_value=plan):
            with self.assertRaisesRegex(ValueError, "duration from 1 to 10"):
                writer.plan_and_write("Een boot", [("image17", None)])

    def test_writer_failure_reuses_saved_plan_and_rejects_unknown_references(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            with patch.object(writer, "ask_qwen", side_effect=[shot_plan(), OSError("offline")]):
                with self.assertRaises(OSError):
                    writer.plan_and_write("Een boot", [("image17", None)], project)
            self.assertTrue((project / "plan.json").is_file())
            with patch.object(writer, "ask_qwen", return_value={"prompts": ["@image99 boat"] * 5}) as call:
                with self.assertRaisesRegex(ValueError, "unknown picture slot"):
                    writer.plan_and_write("Een boot", [("image17", None)], project)
                self.assertEqual(call.call_count, 1)
            with patch.object(writer, "ask_qwen", return_value={"prompts": ["@image17 boat"] * 5}):
                result = writer.plan_and_write("Een boot", [("image17", None)], project)
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
            response = self.post("/projects", {"story": "Een boot", "images": [image_data()]})
            self.assertEqual(response.status, 202)
            identifier = json.load(response)["id"]
            schedule.assert_called_once()
        project = web.ROOT / identifier
        (project / "film.mp4").write_bytes(b"0123456789")
        status = json.load(urlopen(self.url + "/projects/" + identifier))
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["story"], "Een boot")
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

    def test_rejects_cross_origin_and_corrupt_images_without_leaving_project(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/projects", {}, origin="http://example.com")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.post("/projects", {"story": "Een boot", "images": [base64.b64encode(b"not a picture").decode()]})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(list(web.ROOT.iterdir()), [])

    def test_retry_cannot_enqueue_same_project_twice(self):
        project = web.stage({"story": "Een boot", "images": [image_data()]})
        with patch.object(web.QUEUE, "submit") as submit:
            try:
                self.assertTrue(web.schedule(project, resume=True))
                self.assertFalse(web.schedule(project, resume=True))
                submit.assert_called_once_with(web.render, project, True)
            finally:
                web.ACTIVE.discard(project.name)

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
        payload = json.dumps({"story": "Een boot", "images": [image_data()]}).encode()
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
    def test_project_writer_prompt_reaches_the_production_worker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(web, "ROOT", Path(folder)):
            instructions = "Keep the camera locked and describe each movement."
            project = web.stage({"story": "Een boot", "images": [image_data()], "writer_prompt": instructions})
            image = project / "reference-01.png"
            args = SimpleNamespace(project=project, story=project / "story.txt",
                                   references=[image], profile="studio-turbo8")
            with (
                patch.object(run_studio, "wait_for_models"),
                patch.object(run_studio, "plan_and_write", side_effect=OSError("offline")) as planner,
                self.assertRaisesRegex(OSError, "offline"),
            ):
                run_studio.produce(args)
            planner.assert_called_once_with("Een boot", [("image17", image)], project,
                                            writer_prompt=instructions)

    def test_lost_render_response_reuses_request_and_saved_prompts(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            story = project / "story.txt"
            story.write_text("Een boot")
            image = project / "reference-01.png"
            image.write_bytes(base64.b64decode(image_data()))
            plan = shot_plan(7)
            for shot in plan["shots"]:
                shot["prompt"] = "@image17 boat moves"
            client = unittest.mock.MagicMock()
            client.capabilities.return_value = {"profiles": [{"id": "studio-turbo8", "ready": True}]}
            client.upload.return_value = {"id": "asset"}
            client.create.return_value = {"id": "project", "revision": 1}
            client.plan.return_value = {"plan_sha256": "hash"}
            client.render.side_effect = [OSError("response lost"), {"id": "job"}]
            client.wait.return_value = {"artifacts": [{"kind": "box", "unit_id": f"box:shot-{i:02d}"} for i in range(1,8)]}
            client.download.side_effect = lambda artifact, target: target.write_bytes(b"clip")
            args = SimpleNamespace(project=project, story=story, references=[image], profile="studio-turbo8", resume=False)
            def export(root, target):
                self.assertEqual(len(list((root / "clips").glob("*.mp4"))), 7)
                target.write_bytes(b"movie")
            with (
                patch.object(run_studio, "RenderClient", return_value=client),
                patch.object(run_studio, "qwen_ready", return_value=True),
                patch.object(run_studio, "plan_and_write", return_value=plan) as planner,
                patch.object(run_studio.time, "sleep"),
                patch.object(run_studio, "export_project", side_effect=export),
            ):
                with self.assertRaises(OSError):
                    run_studio.produce(args)
                saved_key = read_json(project / "production.json")["key"]
                self.assertEqual(run_studio.produce(args).read_bytes(), b"movie")
                planner.assert_called_once()
                client.create.assert_called_once()
                self.assertEqual(client.render.call_args_list[0], client.render.call_args_list[1])
                self.assertEqual(client.render.call_args.args[2], saved_key)
                # A completed project does not submit additional GPU work.
                self.assertEqual(run_studio.produce(args).read_bytes(), b"movie")
                self.assertEqual(client.render.call_count, 2)


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
