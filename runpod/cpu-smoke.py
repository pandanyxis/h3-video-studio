"""Prove ComfyUI starts and exposes every backend class used in our workflows."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

subprocess.run(['/usr/bin/python3', '/opt/combined/prepare-comfy.py'], check=True)

# Exercise MiniMax previews too: starting the server alone missed a TAESD crash.
subprocess.run(['/opt/venv/bin/python', '-c', '''
import sys
sys.path.insert(0, '/ComfyUI')
from comfy.cli_args import args, LatentPreviewMethod
args.cpu = True
args.preview_method = LatentPreviewMethod.Auto
import torch
import latent_preview
from comfy.latent_formats import MiniMaxH3Video
previewer = latent_preview.get_previewer(torch.device('cpu'), MiniMaxH3Video())
image = previewer.decode_latent_to_preview(torch.zeros(1, 24, 1, 16, 16))
assert image.size == (16, 16)
print('MiniMax automatic preview decoded successfully.', flush=True)
'''], cwd='/ComfyUI', check=True)

process = subprocess.Popen([
    '/opt/venv/bin/python', '/ComfyUI/main.py', '--cpu',
    '--listen', '127.0.0.1', '--port', '8188', '--disable-auto-launch',
    '--base-directory', '/workspace/ComfyUI',
    '--extra-model-paths-config', '/workspace/combined/model-paths.yaml',
    '--disable-dynamic-vram', '--preview-method', 'auto',
], cwd='/ComfyUI')
try:
    deadline = time.monotonic() + 180
    while True:
        if process.poll() is not None:
            raise RuntimeError('ComfyUI exited during CPU startup')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8188/object_info', timeout=5) as response:
                classes = json.load(response)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise RuntimeError('ComfyUI startup timed out')
            time.sleep(2)
    required = set()
    for path in Path('/opt/combined').glob('workflow-*.json'):
        required.update(n['type'] for n in json.loads(path.read_text())['nodes'])
    missing = required - set(classes) - {'Note', 'MarkdownNote'}
    if missing:
        raise RuntimeError('Workflow classes unavailable: ' + ', '.join(sorted(missing)))
    print('ComfyUI CPU startup and all personal workflow classes passed.', flush=True)
finally:
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
