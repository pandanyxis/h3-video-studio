"""Download requested Hugging Face model files into ComfyUI's model folders."""
import json
import os
from pathlib import Path, PurePosixPath
from shutil import copyfile
from tempfile import NamedTemporaryFile

from huggingface_hub import hf_hub_download

ROOT = Path("/workspace/ComfyUI/models")
FOLDERS = {"checkpoints", "diffusion_models", "loras", "text_encoders", "vae", "vae_approx"}


def main():
    entries = json.loads(os.environ.get("HF_EXTRA_MODELS", "[]"))
    if not isinstance(entries, list):
        raise ValueError("HF_EXTRA_MODELS must be a JSON list")

    for entry in entries:
        folder = entry["folder"]
        filename = entry["file"]
        name = PurePosixPath(filename).name
        if folder not in FOLDERS or name in {"", ".", ".."} or "\\" in name:
            raise ValueError("Invalid Hugging Face model destination")
        target = ROOT / folder / name
        if target.exists():
            print(f"[models] Using existing extra: {folder}/{name}", flush=True)
            continue
        try:
            cached = hf_hub_download(
                entry["repo"], filename, revision=entry["revision"]
            )
        except Exception as error:
            raise RuntimeError(
                f"Hugging Face extra {folder}/{name} failed ({type(error).__name__})"
            ) from None
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as temp:
            staging = Path(temp.name)
        try:
            copyfile(cached, staging)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

