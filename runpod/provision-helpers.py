"""Download and verify the standard MiniMax H3 model set."""
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from huggingface_hub import hf_hub_download
from download_progress import LogProgress

ASSETS = Path(__file__).resolve().parent
SOURCES = {
    'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors': ('Comfy-Org/MiniMax-H3', 'e5eb578a89295337b8ff433a035929ce0279e0b6'),
    'text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors': ('Comfy-Org/MiniMax-H3', 'e5eb578a89295337b8ff433a035929ce0279e0b6'),
    'vae/minimax_h3_audio_vae_fp32.safetensors': ('Comfy-Org/MiniMax-H3', 'e5eb578a89295337b8ff433a035929ce0279e0b6'),
    'vae/minimax_h3_video_vae_fp16.safetensors': ('Comfy-Org/MiniMax-H3', 'e5eb578a89295337b8ff433a035929ce0279e0b6'),
    'loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors': ('lightx2v/Minimax-h3-Turbo', '30f485ed874aa44c8183348ad361c5eabad318da'),
    'vae_approx/taeh3.safetensors': ('Kijai/MiniMax-H3-TAE', 'a213ac8bf2f148b4f32372279a7f207846978900'),
}


@contextmanager
def phase(label):
    started = time.monotonic()
    stop = threading.Event()
    def report():
        while not stop.wait(15):
            print(f'[models] {label} - still active ({time.monotonic() - started:.0f}s)', flush=True)
    print('[models] ' + label, flush=True)
    worker = threading.Thread(target=report, daemon=True)
    worker.start()
    try:
        yield
    except Exception as error:
        # Exception messages may contain signed download URLs.
        print(f'[models] FAILED: {label} ({type(error).__name__})', flush=True)
        raise
    finally:
        stop.set()
        worker.join()


def main():
    root = Path('/workspace/ComfyUI/models')
    manifest = {m['path'].removeprefix('ComfyUI/models/'): m for m in json.loads(
        (ASSETS / 'model-manifest.json').read_text())}
    status_path = Path('/tmp/hf_download_status.json')
    status = {name: {'status': 'pending'} for name in SOURCES}
    status_path.write_text(json.dumps(status))
    for index, (name, (repo, revision)) in enumerate(SOURCES.items(), 1):
        model = manifest[name]
        target = root / name
        def valid():
            if not target.is_file() or target.stat().st_size != model['size']:
                return False
            with phase(f'SHA-256 check: {name}'), target.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest() == model['sha256']
        print(f'[models] Helper {index}/{len(SOURCES)}: {name} ({model["size"] / 1024**3:.2f} GiB)', flush=True)
        if not valid():
            remote = Path(name).name if repo == 'lightx2v/Minimax-h3-Turbo' else name
            local = root / Path(name).parent if remote != name else root
            status[name]['status'] = 'downloading'
            status_path.write_text(json.dumps(status))
            print(f'[models] Downloading {name} from {repo}', flush=True)
            hf_hub_download(repo, remote, revision=revision, local_dir=local,
                            force_download=target.exists(), tqdm_class=LogProgress)
            if not valid():
                raise ValueError('Helper weight failed verification: ' + name)
        status[name]['status'] = 'done'
        status_path.write_text(json.dumps(status))
        print('[models] Verified ' + name, flush=True)


if __name__ == '__main__':
    main()
