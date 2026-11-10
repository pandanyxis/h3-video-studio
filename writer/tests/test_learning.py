import asyncio
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from h3_standalone.learning import CAPTURE, Learning, LearningStore, parse_json, video_frames, analysis_error, validation_payload


class StoreTests(unittest.TestCase):
    def test_validation_does_not_repeat_guides_or_include_media_manifests(self):
        record = {"input": {"mode": "I2VA", "creative_brief": "A person walks.", "media_manifest": {"huge": "x" * 50000}},
                  "messages": [{"role": "system", "name": "prompt_studio_system_prompt", "content": "Keep H3 format."},
                               {"role": "system", "name": "official_minimax_h3_guide", "content": "z" * 50000}]}
        payload = validation_payload(record, [record] * 5, {"rules": ""}, "Clear actions", {"summary": "Visible subject"})
        self.assertLess(len(payload), 14000)
        value = json.loads(payload)
        self.assertEqual(len(value["cases"]), 1)
        self.assertEqual(value["writing_contracts"], ["Keep H3 format."])
        self.assertNotIn("media_manifest", payload)

    def test_validation_preserves_complete_briefs_and_rejects_oversize(self):
        record = {"input": {"creative_brief": "x" * 20000}, "messages": []}
        with self.assertRaises(ValueError):
            validation_payload(record, [], {"rules": ""}, "Clear actions", {})

    def test_provider_error_shows_code_and_stage_without_remote_secrets(self):
        error = Exception("secret-api-key")
        error.code = "API_PERMISSION_DENIED"
        error.details = {"status": 403, "reason": "secret-api-key"}
        message = analysis_error(error, "Reviewing video")
        self.assertIn("HTTP 403", message)
        self.assertIn("API_PERMISSION_DENIED", message)
        self.assertIn("Reviewing video", message)
        self.assertNotIn("secret-api-key", message)

    def test_persistence_scope_and_reset(self):
        with tempfile.TemporaryDirectory() as root:
            store = LearningStore(root)
            version = store.put("version", {"scope": "qwen:T2VA", "rules": "Use clear actions"})
            store.setting("active:qwen:T2VA", version["id"])
            reopened = LearningStore(root)
            self.assertEqual(reopened.active("qwen:T2VA")["rules"], "Use clear actions")
            self.assertEqual(reopened.active("other:T2VA")["rules"], "")
            reopened.setting("active:qwen:T2VA", "")
            self.assertIsNone(reopened.active("qwen:T2VA")["id"])

    def test_json_rejects_non_object(self):
        with self.assertRaises(ValueError):
            parse_json("[]")
        self.assertEqual(parse_json('```json\n{"accept": true}\n```'), {"accept": True})

    def test_invalid_backslash_escape_is_preserved_as_literal(self):
        self.assertEqual(parse_json(r'{"summary":"body\_shape and \- edges","accept":false}'),
                         {"summary": r"body\_shape and \- edges", "accept": False})

    def test_valid_json_escapes_remain_unchanged(self):
        value = {"summary": "Line one\nLine two", "path": r"C:\videos\result.mp4", "accept": False}
        self.assertEqual(parse_json(json.dumps(value)), value)

    def test_truncated_json_is_not_guessed(self):
        with self.assertRaises(ValueError):
            parse_json('{"accept": tr')

    def test_real_video_sampling(self):
        import av
        from PIL import Image
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sample.mp4"
            with av.open(str(path), "w") as container:
                stream = container.add_stream("mpeg4", rate=8)
                stream.width, stream.height, stream.pix_fmt = 128, 128, "yuv420p"
                for index in range(16):
                    frame = av.VideoFrame.from_image(Image.new("RGB", (128, 128), (index * 15, 30, 50)))
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            frames, duration = video_frames(path)
            self.assertEqual(len(frames), 16)
            self.assertAlmostEqual(duration, 2)
            self.assertIn("data:image/jpeg;base64,", frames[1]["image_url"]["url"])


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from h3_standalone.app import _prepare_environment
        from h3_standalone.config import load_settings
        _prepare_environment(load_settings(no_browser=True))
        self.temp = tempfile.TemporaryDirectory()
        self.connection = SimpleNamespace(id="connection", models=[{"remote_model": "qwen", "capabilities": {"images": True}}], base_url="https://example.invalid/v1")
        backend = SimpleNamespace(_connections_lock=threading.RLock(), _connections={"connection": self.connection},
            _get_connection=lambda ident: self.connection,
            resolve_model=lambda ref: {"capabilities": {"images": True}})
        def assemble(body, *args):
            return {"input": {"creative_brief": body["creative_brief"], "mode": body["mode"]},
                    "media_inputs": [], "messages": [{"role": "system", "content": "Keep H3 format"}, {"role": "user", "content": body["creative_brief"]}]}
        self.services = SimpleNamespace(API_PROVIDER_BACKEND=backend, assemble_request=assemble, assemble_refinement=assemble)
        self.learning = Learning(Path(self.temp.name), self.services)
        app = web.Application()
        self.learning.install(app)
        async def generate(request):
            self.services.assemble_request(await request.json())
            return web.json_response({"prompt": "A person walks."})
        app.router.add_post("/h3studio/generate", generate)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def generation(self):
        response = await self.client.post("/h3studio/generate", json={"mode": "T2VA", "creative_brief": "Walking in rain", "api_provider": {"model_id": "qwen"}})
        return await response.json()

    async def test_capture_and_injection_and_disable(self):
        result = await self.generation()
        record = self.learning.store.get(result["learning_generation_id"])
        self.assertEqual(record["scope"], "qwen:T2VA")
        version = self.learning.store.put("version", {"scope": record["scope"], "rules": "Prefer observable actions"})
        self.learning.store.setting("active:qwen:T2VA", version["id"])
        result = await self.generation()
        record = self.learning.store.get(result["learning_generation_id"])
        self.assertIn("Prefer observable actions", record["messages"][-1]["content"])
        self.assertEqual(record["version"], version["id"])
        self.learning.store.setting("enabled", False)
        result = await self.generation()
        record = self.learning.store.get(result["learning_generation_id"])
        self.assertIsNone(record["version"])
        self.assertEqual(len(record["messages"]), 2)
        self.assertIsNone(CAPTURE.get())

    async def test_i2v_fidelity_is_applied_and_explicit_changes_take_priority(self):
        response = await self.client.post("/h3studio/generate", json={"mode": "I2VA", "creative_brief": "Change the jacket to blue.", "api_provider": {"model_id": "qwen"}})
        record = self.learning.store.get((await response.json())["learning_generation_id"])
        contract = next(m["content"] for m in record["messages"] if m.get("name") == "i2v_reference_fidelity")
        self.assertIn("clothing", contract)
        self.assertIn("facial structure", contract)
        self.assertIn("Requested changes and actions take priority", contract)
        self.assertEqual(record["model"], "qwen")

    async def test_upload_background_learning_and_cleanup(self):
        result = await self.generation()
        ident = result["learning_generation_id"]
        form = FormData()
        form.add_field("video", io.BytesIO(b"fake video"), filename="video.mp4", content_type="video/mp4")
        form.add_field("used_prompt", "My edited actual prompt")
        responses = iter([{"refine_instruction": "Clarify walking posture while preserving the brief and H3 format.", "summary": "Subject is visible", "observations": ["walking"], "uncertainties": ["limited motion evidence"], "proposed_rules": ["When appropriate, name the visible action."]}, {"accept": True, "reason": "Conditional and compatible"}])
        def chat(*args):
            return {"choices": [{"message": {"content": json.dumps(next(responses))}}]}
        # Import provider classes after configuring the app's existing import boundary.
        from h3_standalone.app import _prepare_environment
        from h3_standalone.config import load_settings
        _prepare_environment(load_settings(no_browser=True))
        with patch("h3_standalone.learning.video_frames", return_value=([{"type": "text", "text": "frame"}], 2)), patch("backend.models.api_provider_backend.ApiProviderBackend._request_chat_completion_stream", side_effect=chat):
            response = await self.client.post(f"/learning/upload/{ident}?connection_id=connection&model_id=qwen", data=form)
            self.assertEqual(response.status, 202, await response.text())
            await asyncio.gather(*self.learning.tasks)
        job = self.learning.store.items("job")[0]
        self.assertEqual(job["status"], "done", job)
        self.assertIn("walking posture", job["refine_instruction"])
        version = self.learning.store.active("qwen:T2VA")
        self.assertEqual(version["used_prompt"], "My edited actual prompt")
        self.assertEqual(version["validation_type"], "model_text_review")
        self.assertFalse(list(Path(self.temp.name).glob("*.upload")))

    async def test_invalid_video_fails_without_changing_active_rules(self):
        result = await self.generation()
        record = self.learning.store.get(result["learning_generation_id"])
        path = Path(self.temp.name) / "bad.upload"
        path.write_bytes(b"not a video")
        job = self.learning.store.put("job", {"generation_id": record["id"], "status": "queued"})
        await self.learning.run(job, record, path, self.connection, "qwen")
        self.assertEqual(self.learning.store.get(job["id"])["status"], "failed")
        self.assertIsNone(self.learning.store.active("qwen:T2VA")["id"])
        self.assertFalse(path.exists())

    async def test_rejected_version_cannot_be_activated(self):
        version = self.learning.store.put("version", {"scope": "qwen:T2VA", "rules": "Bad rule", "status": "rejected"})
        response = await self.client.post("/learning/config", json={"version_id": version["id"]})
        self.assertEqual(response.status, 400)
        self.assertIsNone(self.learning.store.active("qwen:T2VA")["id"])

    async def test_remove_and_restore_analysis_without_deleting_history(self):
        job = self.learning.store.put("job", {"status": "done", "generation_id": "example"})
        response = await self.client.post("/learning/archive", json={"id": job["id"], "archived": True})
        self.assertEqual(response.status, 200)
        state = await (await self.client.get("/learning/state")).json()
        self.assertEqual(state["jobs"], [])
        state = await (await self.client.get("/learning/state?archived=1")).json()
        self.assertTrue(state["jobs"][0]["archived"])
        response = await self.client.post("/learning/archive", json={"id": job["id"], "archived": False})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.learning.store.items("job")), 1)

    async def test_active_version_and_running_analysis_cannot_be_removed(self):
        version = self.learning.store.put("version", {"scope": "qwen:T2VA", "status": "accepted", "rules": "Clear actions"})
        self.learning.store.setting("active:qwen:T2VA", version["id"])
        job = self.learning.store.put("job", {"status": "running", "generation_id": "example"})
        for item in [version, job]:
            response = await self.client.post("/learning/archive", json={"id": item["id"], "archived": True})
            self.assertEqual(response.status, 409)
            self.assertFalse(self.learning.store.get(item["id"]).get("archived", False))

    async def test_state_never_exposes_reference_payloads_or_credentials(self):
        await self.generation()
        response = await self.client.get("/learning/state")
        state = await response.json()
        self.assertEqual(state["models"][0]["model_id"], "qwen")
        self.assertNotIn("references", state["generations"][0])
        self.assertNotIn("messages", state["generations"][0])
        self.assertNotIn("api_key", json.dumps(state))


if __name__ == "__main__":
    unittest.main()
