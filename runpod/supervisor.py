"""Run the personal startup scripts in separate process groups."""
import os
import signal
import subprocess
import time


def enabled(name):
    value = os.environ.get(name, "true")
    if value not in ("true", "false"):
        raise SystemExit(f"{name} must be true or false")
    return value == "true"


def main():
    comfy, qwen = [enabled(n) for n in ("ENABLE_COMFYUI", "ENABLE_QWEN")]
    writer = os.environ.get("ENABLE_WRITER", "false") == "true"
    if not (comfy or qwen or writer):
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
        if writer:
            commands.append(["bash", "/opt/combined/start-writer.sh"])
        if comfy:
            commands.append(["bash", os.environ.get("COMFY_START_SCRIPT", "/start_script.sh")])
        if qwen:
            commands.append(["bash", "/usr/local/bin/start-services.sh"])
        for cmd in commands:
            children.append(subprocess.Popen(cmd, start_new_session=True))
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    print(f"Startup service exited: {child.args[1]} ({child.returncode})", flush=True)
                    return 1
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
