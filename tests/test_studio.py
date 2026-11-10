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
from studio_storage import atomic_json, read_json
import studio_web as web
import studio_writer as writer


def image_data():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def shot_plan():
    return {"shots": [{"action": "A boat drifts", "camera": "Wide shot", "sound": "Water",
                       "start": "Boat left", "end": "Boat right"} for _ in range(5)]}


class WriterTests(unittest.TestCase):
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

    def test_rejects_cross_origin_and_corrupt_images_without_leaving_project(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/projects", {}, origin="http://example.com")
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.post("/projects", {"story": "Een boot", "images": [base64.b64encode(b"not a picture").decode()]})
        self.assertEqual(error.exception.code, 400)
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


class ProductionTests(unittest.TestCase):
    def test_lost_render_response_reuses_request_and_saved_prompts(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            story = project / "story.txt"
            story.write_text("Een boot")
            image = project / "reference-01.png"
            image.write_bytes(base64.b64decode(image_data()))
            plan = shot_plan()
            for shot in plan["shots"]:
                shot["prompt"] = "@image17 boat moves"
            client = unittest.mock.MagicMock()
            client.capabilities.return_value = {"profiles": [{"id": "studio-turbo8", "ready": True}]}
            client.upload.return_value = {"id": "asset"}
            client.create.return_value = {"id": "project", "revision": 1}
            client.plan.return_value = {"plan_sha256": "hash"}
            client.render.side_effect = [OSError("response lost"), {"id": "job"}]
            client.wait.return_value = {"artifacts": [{"kind": "box", "unit_id": f"box:shot-{i:02d}"} for i in range(1,6)]}
            client.download.side_effect = lambda artifact, target: target.write_bytes(b"clip")
            args = SimpleNamespace(project=project, story=story, references=[image], profile="studio-turbo8", resume=False)
            def export(root, target):
                self.assertEqual(len(list((root / "clips").glob("*.mp4"))), 5)
                target.write_bytes(b"movie")
            with patch.object(run_studio, "RenderClient", return_value=client), patch.object(run_studio, "plan_and_write", return_value=plan) as planner, patch.object(run_studio.time, "sleep"), patch.object(run_studio, "export_project", side_effect=export):
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
