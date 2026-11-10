"""Initialize workspace access, SSH and personal workflows for the prebuilt image."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT = Path('/workspace/combined')


def log(message):
    print('[combined] ' + message, flush=True)


def run(args):
    subprocess.run([str(x) for x in args], check=True)


def prepare_access():
    """Use supplied secrets, or persist generated per-pod credentials privately."""
    path = ROOT / 'access.json'
    saved = json.loads(path.read_text()) if path.exists() else {}
    for key, fallback in {
        'LLAMA_API_KEY': lambda: secrets.token_hex(32),
        'JUPYTER_TOKEN': lambda: secrets.token_hex(32),
    }.items():
        value = os.environ.get(key) or saved.get(key) or fallback()
        os.environ[key] = saved[key] = value
    if len(saved['LLAMA_API_KEY']) < 32:
        raise ValueError('LLAMA_API_KEY must contain at least 32 characters')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(saved, handle, indent=2)
    log('Login details are saved privately in /workspace/combined/access.json; values are not logged.')



def prepare_ssh():
    public_keys = os.environ.get('PUBLIC_KEY', '').strip()
    if not public_keys or public_keys == 'null':
        return
    ssh_dir = Path('/root/.ssh')
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized = ssh_dir / 'authorized_keys'
    current = authorized.read_text().splitlines() if authorized.exists() else []
    for key in public_keys.splitlines():
        if key.strip() and key not in current:
            current.append(key)
    authorized.write_text('\n'.join(current) + '\n')
    authorized.chmod(0o600)
    Path('/run/sshd').mkdir(exist_ok=True)
    run(['ssh-keygen', '-A'])
    run(['/usr/sbin/sshd'])
    log('SSH ready on port 22 with registered public keys.')


def install_workflows():
    destination = Path('/workspace/ComfyUI/user/default/workflows/Personal')
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(Path('/opt/combined').glob('workflow-*.json')):
        name = source.name
        target = destination / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
    log('Personal T2V and I2V workflows installed; existing edited copies are preserved.')
