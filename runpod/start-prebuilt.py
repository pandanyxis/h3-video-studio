"""Start the prebuilt image. No apt, pip, git build or CUDA compilation here."""
import os
from bootstrap import ROOT, install_workflows, prepare_access, prepare_ssh

ROOT.mkdir(parents=True, exist_ok=True)
prepare_ssh()
prepare_access()
install_workflows()
print('[combined] Prebuilt runtime ready; starting ComfyUI provisioning and Qwen.', flush=True)
os.execv('/usr/bin/python3', ['/usr/bin/python3', '-u', '/opt/combined/supervisor.py'])
