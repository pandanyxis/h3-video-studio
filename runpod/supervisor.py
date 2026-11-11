"""Run the personal startup scripts in separate process groups."""
import json
import os
import signal
import subprocess
import time
from urllib.request import urlopen


def enabled(name):
    value = os.environ.get(name, "true")
    if value not in ("true", "false"):
        raise SystemExit(f"{name} must be true or false")
    return value == "true"


def studio_ready():
    try:
        for url in ("http://127.0.0.1:8190/health", "http://127.0.0.1:8080/health"):
            with urlopen(url, timeout=2):
                pass
        with urlopen("http://127.0.0.1:8188/h3c/api/v1/capabilities", timeout=2) as response:
            profiles = json.load(response)["profiles"]
        return any(profile["id"] == "studio-turbo8" and profile.get("ready") for profile in profiles)
    except (OSError, ValueError, KeyError):
        return False


def main():
    comfy, qwen = [enabled(n) for n in ("ENABLE_COMFYUI", "ENABLE_QWEN")]
    studio = enabled("ENABLE_STUDIO") and comfy and qwen
    if not (comfy or qwen):
        raise SystemExit("Enable at least one service")
    if qwen and len(os.environ.get("LLAMA_API_KEY", "")) < 32:
        raise SystemExit("Set LLAMA_API_KEY to at least 32 random characters")
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        commands = []
        if studio:
            commands.append(["/opt/venv/bin/python", "/opt/studio/studio_web.py"])
        if comfy:
            commands.append(["bash", os.environ.get("COMFY_START_SCRIPT", "/start_script.sh")])
        if qwen:
            commands.append(["bash", "/usr/local/bin/start-services.sh"])
        for cmd in commands:
            children.append(subprocess.Popen(cmd, start_new_session=True))
        announced = False
        next_probe = time.monotonic()
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    print(f"Startup service exited: {child.args[1]} ({child.returncode})", flush=True)
                    return 1
            if studio and not announced and time.monotonic() >= next_probe:
                if studio_ready():
                    print("[studio] Klaar om te renderen: Studio, Qwen en ComfyUI zijn beschikbaar.", flush=True)
                    announced = True
                next_probe = time.monotonic() + 10
            time.sleep(1)
        return 0
    finally:
        # Include ComfyUI/Jupyter children spawned by the ComfyUI shell script.
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and any(c.poll() is None for c in children):
            time.sleep(0.2)
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
