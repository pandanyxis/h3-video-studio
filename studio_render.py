"""Studio's HTTP adapter for the pinned H3 Continuity rendering API."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

PREFIX = "/h3c/api/v1"
MODEL_OPTIONS = {
    "models": ("UNETLoader", "unet_name"),
    "loras": ("LoraLoaderModelOnly", "lora_name"),
}


class RenderError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RenderError("The render service returned an unexpected redirect.")


class RenderClient:
    def __init__(self, base_url="http://127.0.0.1:8188"):
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(NoRedirect())

    def url(self, path):
        parts = urlsplit(path)
        node_paths = {f"/object_info/{node}" for node, _ in MODEL_OPTIONS.values()}
        if parts.scheme or parts.netloc or (not path.startswith(PREFIX + "/") and path not in node_paths):
            raise RenderError("The render service returned an invalid file URL.")
        return self.base_url + path

    def request(self, path, method="GET", body=None, key=None, headers=None):
        headers = {"Accept": "application/json", **(headers or {})}
        if key:
            headers["Idempotency-Key"] = key
        if isinstance(body, dict):
            body = json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.url(path), data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=60) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                detail = json.load(error).get("error", {}).get("message", str(error.code))
            except (ValueError, AttributeError):
                detail = str(error.code)
            raise RenderError(f"Render service reported: {detail}") from error
        if not isinstance(result, dict):
            raise RenderError("The render service returned an invalid response.")
        return result

    def capabilities(self):
        return self.request(PREFIX + "/capabilities")

    def model_options(self):
        options = {}
        for key, (node, field) in MODEL_OPTIONS.items():
            schema = self.request(f"/object_info/{node}")
            try:
                choices = schema[node]["input"]["required"][field][0]
            except (IndexError, KeyError, TypeError):
                raise RenderError("ComfyUI did not return available model options.") from None
            if not isinstance(choices, list) or any(not isinstance(choice, str) for choice in choices):
                raise RenderError("ComfyUI returned invalid model options.")
            options[key] = sorted(set(choices), key=str.casefold)
        return options

    def create(self, document, key):
        return self.request(PREFIX + "/projects", "POST", document, key)

    def project(self, project_id):
        return self.request(PREFIX + "/projects/" + quote(project_id, safe=""))

    def update_project(self, project_id, document, revision):
        fields = {key: document[key] for key in ("name", "settings", "timeline", "assets", "consumer") if key in document}
        return self.request(PREFIX + "/projects/" + quote(project_id, safe=""),
                            "PUT", dict(fields, expected_revision=revision))

    def plan(self, project_id, selection):
        return self.request(PREFIX + "/projects/" + quote(project_id, safe="") + "/plan", "POST", selection)

    def render(self, project_id, selection, key):
        return self.request(PREFIX + "/projects/" + quote(project_id, safe="") + "/jobs", "POST", selection, key)

    def job(self, job_id):
        return self.request(PREFIX + "/jobs/" + quote(job_id, safe=""))

    def resume(self, job_id):
        return self.request(PREFIX + "/jobs/" + quote(job_id, safe="") + "/resume", "POST", {})

    def cancel(self, job_id):
        return self.request(PREFIX + "/jobs/" + quote(job_id, safe="") + "/cancel", "POST", {})

    def upload(self, path, kind="picture"):
        data = Path(path).read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        boundary = "studio" + uuid.uuid4().hex
        fields = {"kind": kind, "expected_sha256": checksum}
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        )
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reference"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n').encode()
        body += data + f"\r\n--{boundary}--\r\n".encode()
        asset = self.request(PREFIX + "/assets", "POST", body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        if asset.get("sha256") != checksum:
            raise RenderError("The uploaded reference image does not match the original.")
        return asset

    def wait(self, job_id, on_update, deadline=4 * 3600):
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            job = self.job(job_id)
            if on_update(job):
                time.sleep(3)
                continue
            if job.get("state") == "succeeded":
                return job
            if job.get("state") in ("failed", "needs_reconciliation"):
                message = (job.get("error") or {}).get("message", "Check the render service.")
                raise RenderError(f"Rendering interrupted: {message}")
            time.sleep(3)
        raise RenderError("The render timed out. Resume this project to check it again.")

    def download(self, artifact, destination):
        size, checksum = artifact.get("size"), artifact.get("sha256")
        if type(size) is not int or size <= 0 or not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise RenderError("The render service returned invalid file metadata.")
        destination = Path(destination)
        if destination.is_file() and destination.stat().st_size == size:
            with destination.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == checksum:
                    return
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        hasher = hashlib.sha256()
        count = 0
        try:
            with self.opener.open(self.url(artifact["download_url"]), timeout=120) as response, temporary.open("xb") as stream:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise RenderError("The video file is larger than expected.")
                    hasher.update(chunk)
                    stream.write(chunk)
                if count != size or hasher.hexdigest() != checksum:
                    raise RenderError("The video file is incomplete or damaged.")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
