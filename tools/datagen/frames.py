"""Cut exactly the frames a dataset uses from an episode video."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.paths import temporary_dir

FFMPEG = shutil.which("ffmpeg") or str(Path(sys.executable).with_name("ffmpeg"))
BATCH = 4000  # frame indices per ffmpeg pass (each pass decodes up to its last index)


def frame_name(index: int) -> str:
    return f"{index:06d}.jpg"


def cut_frames(video: Path, indices: list[int], out_dir: Path, *, fps: int, jpeg_quality: int) -> int:
    """Write frame `i` of the video resampled to `fps` as out_dir/{i:06d}.jpg for every i.
    Indices past the last decodable frame get the last frame (a timestamp rounded up at
    the very end of a recording). Returns how many indices needed that fallback."""
    wanted = sorted(set(indices))
    out_dir.mkdir(parents=True, exist_ok=True)
    clamped = 0
    last_written: Path | None = None
    for start in range(0, len(wanted), BATCH):
        batch = wanted[start:start + BATCH]
        select = "+".join(f"eq(n\\,{i})" for i in batch)
        with tempfile.TemporaryDirectory(dir=temporary_dir("datagen")) as tmp:
            subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-threads", "1", "-i", str(video), "-an",
                 "-filter_threads", "1", "-threads", "1", "-frames:v", str(len(batch)),
                 "-vf", f"fps={fps},select={select}", "-fps_mode", "passthrough",
                 "-q:v", str(jpeg_quality), "-start_number", "0", "-y", f"{tmp}/%06d.jpg"],
                check=True,
            )
            written = sorted(Path(tmp).glob("*.jpg"))
            if not written and last_written is None:
                raise RuntimeError(f"ffmpeg wrote no frames for {video}")
            for position, index in enumerate(batch):
                target = out_dir / frame_name(index)
                if position < len(written):
                    shutil.move(str(written[position]), target)
                    last_written = target
                else:
                    shutil.copyfile(last_written, target)
                    clamped += 1
    return clamped
