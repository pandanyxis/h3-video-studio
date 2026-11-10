"""Download exact Civitai files with our SHA256-verified downloader."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ASSETS = Path(__file__).resolve().parent
STATUS = Path('/tmp/combined_civitai_status.json')
DOWNLOADER = str(ASSETS / 'download-civitai.py')


def verified(path, model):
    if not path.is_file() or path.stat().st_size != model['size']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == model['sha256']


def provision(models_root):
    models_root = Path(models_root)
    models = json.loads((ASSETS / 'civitai-models.json').read_text(encoding='utf-8'))
    mirror_repo = os.environ.get('HF_CIVITAI_MIRROR_REPO', '').strip()
    mirror_revision = os.environ.get('HF_CIVITAI_MIRROR_REVISION', '').strip()
    if mirror_repo and (len(mirror_revision) != 40 or any(c not in '0123456789abcdef' for c in mirror_revision)):
        raise ValueError('Pin HF_CIVITAI_MIRROR_REVISION to the verified 40-character commit SHA')
    extras = [entry.strip() for entry in os.environ.get(
        'CIVITAI_DIFFUSION_MODELS', '').split(',') if entry.strip()]
    status = {m['name']: 'pending' for m in models}
    status.update({f'extra_diffusion_{i}': 'pending' for i in range(len(extras))})
    STATUS.write_text(json.dumps(status), encoding='utf-8')
    for model in models:
        target = models_root / model['subdir'] / model['name']
        target.parent.mkdir(parents=True, exist_ok=True)
        if not verified(target, model):
            # Token stays in the environment; never put credentials in arguments.
            if mirror_repo:
                command = ['/opt/hf/bin/python', str(ASSETS / 'download-mirror.py'),
                           '--repo', mirror_repo, '--revision', mirror_revision,
                           '--path', model['subdir'] + '/' + model['name'],
                           '--root', str(models_root)]
            else:
                command = [sys.executable, DOWNLOADER, '--identifier', model['identifier'],
                           '--output', str(target.parent), '--filename', model['name']]
            result = subprocess.run(command)
            if result.returncode != 0 or not verified(target, model):
                status[model['name']] = 'failed'
                STATUS.write_text(json.dumps(status), encoding='utf-8')
                return 1
        status[model['name']] = 'done'
        STATUS.write_text(json.dumps(status), encoding='utf-8')
        print('[combined] Verified Civitai file: ' + model['name'], flush=True)
    for index, identifier in enumerate(extras):
        destination = models_root / 'diffusion_models'
        destination.mkdir(parents=True, exist_ok=True)
        # The downloader resolves the extra file's name and verifies
        # its integrity against Civitai metadata. No filename is guessed here.
        result = subprocess.run([sys.executable, DOWNLOADER,
                                 '--identifier', identifier,
                                 '--output', str(destination)])
        status[f'extra_diffusion_{index}'] = 'done' if result.returncode == 0 else 'failed'
        STATUS.write_text(json.dumps(status), encoding='utf-8')
        if result.returncode != 0:
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(provision(sys.argv[1]))
