import os
import json
from pathlib import Path
import urllib.request


def downloads_ready(hf_status, civitai_status, manifest, civitai_models, workspace):
    # A repeated process start can have an empty queue. Validate actual files.
    if any(item.get('status') != 'done' for item in hf_status.values()):
        return False
    if not {m['name'] for m in civitai_models}.issubset(civitai_status):
        return False
    if any(state != 'done' for state in civitai_status.values()):
        return False
    required = [m for m in manifest if m['path'].startswith('ComfyUI/models/')]
    return bool(required) and all(
        (workspace / m['path']).is_file()
        and (workspace / m['path']).stat().st_size == m['size']
        for m in required)


def main():
    urls = []
    if all(os.environ.get(name, "true") == "true"
           for name in ("ENABLE_STUDIO", "ENABLE_COMFYUI", "ENABLE_QWEN")):
        urls.append("http://127.0.0.1:8190/health")
    if os.environ.get('ENABLE_COMFYUI', 'true') == 'true':
        urls.append('http://127.0.0.1:8188/system_stats')
        if os.environ.get('REQUIRE_MINIMAX_DOWNLOADS', 'false') == 'true':
            assets = Path('/opt/combined')
            if not downloads_ready(
                    json.loads(Path('/tmp/hf_download_status.json').read_text()),
                    {},
                    json.loads((assets / 'model-manifest.json').read_text()),
                    [],
                    Path('/workspace')):
                raise SystemExit('Required ComfyUI model provisioning is incomplete')
    if os.environ.get('ENABLE_QWEN', 'true') == 'true':
        urls.append('http://127.0.0.1:8080/health')
    for url in urls:
        with urllib.request.urlopen(url, timeout=4) as response:
            if response.status != 200:
                raise SystemExit(1)


if __name__ == '__main__':
    main()
