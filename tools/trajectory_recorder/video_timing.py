"""Visual synchronization marker for browser video recordings."""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MARKER_SEQUENCE = (
    "red",
    "green",
    "blue",
    "magenta",
)
MARKER_RGB = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "magenta": (255, 0, 255),
    "cyan": (0, 255, 255),
    "yellow": (255, 255, 0),
    "white": (255, 255, 255),
}

SHOW_MARKER_SCRIPT = """
async ({sequence, warmupMs, holdMs, settleMs}) => {
    const id = "__videoSyncMarker";
    document.getElementById(id)?.remove();
    const marker = document.createElement("div");
    marker.id = id;
    Object.assign(marker.style, {
        position: "fixed",
        left: "16px",
        top: "16px",
        width: "64px",
        height: "64px",
        zIndex: "2147483647",
        pointerEvents: "none",
        opacity: "1",
        border: "0",
        boxShadow: "none",
        transition: "none",
        contain: "strict"
    });
    document.documentElement.appendChild(marker);
    const events = [];
    const realPerfNow = () => window.__realPerfNow ? window.__realPerfNow() : performance.now();
    const realEpochNow = () => window.__realDateNow
        ? window.__realDateNow()
        : performance.timeOrigin + realPerfNow();
    const sleep = (ms) => new Promise(resolve =>
        (window.__realSetTimeout || window.setTimeout)(resolve, ms)
    );
    try {
        marker.style.backgroundColor = "white";
        await sleep(warmupMs);
        for (const color of sequence) {
            marker.style.backgroundColor = {
                red: "#ff0000",
                green: "#00ff00",
                blue: "#0000ff",
                magenta: "#ff00ff",
                cyan: "#00ffff",
                yellow: "#ffff00"
            }[color];
            events.push({
                color,
                epoch_ms: realEpochNow()
            });
            await sleep(holdMs);
        }
    } finally {
        marker.remove();
    }
    await sleep(settleMs);
    return events;
}
"""


@dataclass(frozen=True, slots=True)
class VideoTimingCalibration:
    offset_s: float
    matched_markers: int
    expected_markers: int


def trim_video_to_trajectory_start(
    video_path: Path,
    *,
    offset_s: float,
    bitrate: str,
    fps: int,
    duration_s: float,
    pause_intervals: list[tuple[float, float]] | None = None,
) -> float:
    """Keep the trajectory's active span, returning the residual video offset."""
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    trim_s = max(0.0, -offset_s)
    pauses = _merge_intervals(pause_intervals or [])
    pts = _frame_pts(video_path, duration_s=None)
    if not pts:
        raise RuntimeError("Video has no decodable frames.")
    if trim_s > 0:
        kept_candidates = [pts_value for pts_value in pts if pts_value <= trim_s]
        if not kept_candidates:
            raise RuntimeError("Video has no frame before the calibrated trajectory start.")
        first_kept_pts = kept_candidates[-1]
    else:
        first_kept_pts = pts[0]

    pauses = _merge_intervals(
        [
            (max(start, first_kept_pts), end)
            for start, end in pauses
            if end > first_kept_pts
        ]
    )

    output_path = video_path.with_name("video.trim.webm")
    try:
        if pauses:
            dropped = "+".join(
                f"gte(t\\,{start:.6f})*lt(t\\,{end:.6f})"
                for start, end in pauses
            )
            shifted = "+".join(
                f"gte(PTS*TB\\,{end:.6f})*{end - start:.6f}"
                for start, end in pauses
            )
            video_filter = (
                f"trim=start={first_kept_pts:.6f},"
                f"select=not({dropped}),"
                f"setpts=PTS-STARTPTS-({shifted})/TB,"
                f"fps={fps}"
            )
        else:
            video_filter = (
                f"trim=start={first_kept_pts:.6f},"
                f"setpts=PTS-STARTPTS,fps={fps}"
            )
        video_filter += f",trim=duration={duration_s:.6f}"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-threads",
                "2",
                "-i",
                str(video_path),
                "-vf",
                video_filter,
                "-map",
                "0:v:0",
                "-an",
                "-fps_mode",
                "cfr",
                "-c:v",
                "libsvtav1",
                "-preset",
                "6",
                "-crf",
                "30",
                "-svtav1-params",
                "lp=4",
                "-filter_threads",
                "1",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"ffmpeg failed to rebuild the active video timeline: {result.stderr.strip()}"
            )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced an empty trimmed video.")
        output_path.replace(video_path)
    finally:
        output_path.unlink(missing_ok=True)
    return offset_s + first_kept_pts


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    normalized = sorted(
        (max(0.0, float(start)), max(0.0, float(end)))
        for start, end in intervals
        if float(end) > float(start)
    )
    merged: list[tuple[float, float]] = []
    for start, end in normalized:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


async def show_sync_marker(page: Any) -> list[dict[str, Any]]:
    """Display a short marker sequence and return page-clock transition times."""
    events = await page.evaluate(
        SHOW_MARKER_SCRIPT,
        {
            "sequence": list(MARKER_SEQUENCE),
            "warmupMs": 50,
            "holdMs": 80,
            "settleMs": 50,
        },
    )
    if not isinstance(events, list):
        raise RuntimeError("Synchronization marker returned an invalid event list.")
    return [event for event in events if isinstance(event, dict)]


def calibrate_video_offset(
    video_path: Path,
    marker_events: list[dict[str, Any]],
    trajectory_epoch_ms: float,
    *,
    search_duration_s: float,
) -> VideoTimingCalibration:
    """Map encoded video PTS to trajectory time using the recorded marker."""
    expected = len(MARKER_SEQUENCE)
    if len(marker_events) != expected:
        raise RuntimeError(f"Synchronization marker has {len(marker_events)}/{expected} events.")

    pts = _frame_pts(video_path, duration_s=search_duration_s)
    colors = _decoded_marker_colors(video_path, duration_s=search_duration_s)
    frame_count = min(len(pts), len(colors))
    if frame_count < 2:
        raise RuntimeError("Synchronization marker decoder found no video frames.")

    video_transitions: list[tuple[str, float]] = []
    previous_color = ""
    for color, frame_pts in zip(colors[:frame_count], pts[:frame_count]):
        if color == previous_color:
            continue
        video_transitions.append((color, frame_pts))
        previous_color = color
    matches = _longest_color_alignment(marker_events, video_transitions)

    minimum_matches = max(3, expected - 1)
    if len(matches) < minimum_matches:
        raise RuntimeError(
            f"Synchronization marker matched only {len(matches)}/{expected} transitions."
        )

    offsets = [
        (float(event["epoch_ms"]) - trajectory_epoch_ms) / 1000.0 - frame_pts
        for event, frame_pts in matches
    ]
    center = statistics.median(offsets)
    inliers = [offset for offset in offsets if abs(offset - center) <= 0.1]
    if len(inliers) < max(3, expected - 2):
        raise RuntimeError("Synchronization marker transitions were not time-consistent.")
    return VideoTimingCalibration(
        offset_s=statistics.median(inliers),
        matched_markers=len(inliers),
        expected_markers=expected,
    )


def _longest_color_alignment(
    events: list[dict[str, Any]],
    transitions: list[tuple[str, float]],
) -> list[tuple[dict[str, Any], float]]:
    """Align transitions while tolerating a dropped marker at either edge."""
    rows = len(events)
    columns = len(transitions)
    lengths = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(rows - 1, -1, -1):
        event_color = str(events[row].get("color"))
        for column in range(columns - 1, -1, -1):
            if event_color == transitions[column][0]:
                lengths[row][column] = 1 + lengths[row + 1][column + 1]
            else:
                lengths[row][column] = max(
                    lengths[row + 1][column],
                    lengths[row][column + 1],
                )

    matches: list[tuple[dict[str, Any], float]] = []
    row = 0
    column = 0
    while row < rows and column < columns:
        if (
            str(events[row].get("color")) == transitions[column][0]
            and lengths[row][column] == 1 + lengths[row + 1][column + 1]
        ):
            matches.append((events[row], transitions[column][1]))
            row += 1
            column += 1
        elif lengths[row + 1][column] >= lengths[row][column + 1]:
            row += 1
        else:
            column += 1
    return matches


def _frame_pts(video_path: Path, *, duration_s: float | None = 4.0) -> list[float]:
    command = ["ffprobe", "-v", "error"]
    if duration_s is not None:
        command.extend(["-read_intervals", f"%+{duration_s:g}"])
    command.extend(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            str(video_path),
        ]
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed during video calibration: {result.stderr.strip()}")
    return [
        float(line.strip().split(",", 1)[0])
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _decoded_marker_colors(video_path: Path, *, duration_s: float) -> list[str]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-t",
            f"{duration_s:g}",
            "-i",
            str(video_path),
            "-vf",
            "crop=44:44:26:26,scale=1:1:flags=area",
            "-fps_mode",
            "passthrough",
            "-an",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed during video calibration: {stderr}")
    pixels = result.stdout
    return [
        _nearest_color(pixels[index : index + 3])
        for index in range(0, len(pixels) - 2, 3)
    ]


def _nearest_color(pixel: bytes) -> str:
    rgb = tuple(pixel)
    distances = {
        name: sum(
            (rgb[channel] - reference[channel]) ** 2 for channel in range(3)
        )
        for name, reference in MARKER_RGB.items()
    }
    color = min(distances, key=distances.__getitem__)
    return color if distances[color] <= 120**2 else "unknown"
