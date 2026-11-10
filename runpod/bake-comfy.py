"""Install only the pinned ComfyUI sources used by the personal workflows."""
import json
from pathlib import Path
import subprocess

PYTHON = '/opt/venv/bin/python'


def run(args):
    subprocess.run([str(x) for x in args], check=True)


def install(source, target):
    run(['git', 'init', target])
    run(['git', '-C', target, 'remote', 'add', 'origin', source['url']])
    run(['git', '-C', target, 'fetch', '--depth', '1', 'origin', source['ref']])
    run(['git', '-C', target, 'checkout', '--detach', 'FETCH_HEAD'])
    requirements = target / 'requirements.txt'
    if requirements.exists():
        run([PYTHON, '-m', 'pip', 'install', '--no-cache-dir', '-r', requirements])


sources = json.loads(Path('/opt/combined/comfy-sources.json').read_text())
install(sources['comfyui'], Path('/ComfyUI'))
for source in sources['nodes']:
    name = source['url'].rsplit('/', 1)[-1].removesuffix('.git')
    install(source, Path('/ComfyUI/custom_nodes') / name)
run([PYTHON, '-c', 'import torch; assert torch.version.cuda == "13.0"'])
print('Personal ComfyUI source and three workflow node packs installed.', flush=True)
