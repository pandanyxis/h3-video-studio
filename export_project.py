"""Assemble a project's completed shots into one MP4 on Runpod."""
import argparse
import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


def export_project(project, output):
    project = project.resolve()
    plan = json.loads((project / "shots.json").read_text(encoding="utf-8"))
    count = len(plan["shots"])
    if count not in (5, 10, 20):
        raise ValueError("Expected a plan with 5, 10 or 20 shots")
    names = [f"shot-{index:02d}.mp4" for index in range(1, count + 1)]
    for name in names:
        clip = project / "clips" / name
        if not clip.is_file() or not clip.stat().st_size:
            raise FileNotFoundError(f"Shot incomplete: {clip}")
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=project / "clips") as temp:
        staging = Path(temp)
        playlist = staging / "shots.ffconcat"
        playlist.write_text(
            "ffconcat version 1.0\n" + "".join(f"file ../{name}\n" for name in names),
            encoding="ascii",
        )
        movie = staging / "movie.mp4"
        # Continuity renders must share codec, size, frame rate and audio layout.
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(playlist),
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-movflags", "+faststart", str(movie),
            ],
            check=True,
        )
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            destination = Path(handle.name)
        try:
            shutil.copyfile(movie, destination)
            os.link(destination, output)
        finally:
            destination.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_project(args.project, args.output.resolve())


if __name__ == "__main__":
    main()
