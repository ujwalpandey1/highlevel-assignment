"""Capture a real PTY session as asciicast, then render its screen to H.264.

Optional recording-only dependencies: Pillow and ffmpeg. The application and
offline evaluations do not depend on either. No output is fabricated or replaced.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import termios
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
COLUMNS, ROWS = 112, 36
WIDTH, HEIGHT = 1280, 900


class Terminal:
    """The demo emits printable text, CR/LF, and CSI clear/home only."""

    def __init__(self):
        self.lines = [[" "] * COLUMNS for _ in range(ROWS)]
        self.row = self.column = 0
        self.pending = ""

    def down(self):
        self.row += 1
        if self.row == ROWS:
            self.lines.pop(0)
            self.lines.append([" "] * COLUMNS)
            self.row -= 1

    def feed(self, content):
        content = self.pending + content
        self.pending = ""
        index = 0
        while index < len(content):
            char = content[index]
            if char == "\x1b":
                match = re.match(r"\x1b\[([0-9;]*)([A-Za-z])", content[index:])
                if not match:
                    self.pending = content[index:]
                    break
                if match[2] == "J" and match[1] == "2":
                    self.lines = [[" "] * COLUMNS for _ in range(ROWS)]
                elif match[2] == "H":
                    self.row = self.column = 0
                else:
                    raise ValueError("Unsupported terminal control in recording")
                index += len(match[0])
                continue
            if char == "\r":
                self.column = 0
            elif char == "\n":
                self.down()
            elif char == "\b":
                self.column = max(0, self.column - 1)
            elif char.isprintable():
                if self.column == COLUMNS:
                    self.column = 0
                    self.down()
                self.lines[self.row][self.column] = char
                self.column += 1
            index += 1

    def screen(self):
        return "\n".join("".join(line).rstrip() for line in self.lines)


def capture(destination):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
    command = [str(ROOT / "run"), "demo", "--screen", "--pause", "14"]
    started = time.monotonic()
    timestamp = int(time.time())
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "TERM": "xterm-256color", "PYTHONUNBUFFERED": "1"},
    )
    os.close(slave)
    events = []
    header = {
        "version": 2,
        "width": COLUMNS,
        "height": ROWS,
        "timestamp": timestamp,
        "title": "Pipeline Copilot: real CLI session, replay mode",
        "command": "./run demo --screen --pause 14",
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }
    with destination.open("w") as stream:
        stream.write(json.dumps(header) + "\n")
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                event = [
                    round(time.monotonic() - started, 4),
                    "o",
                    chunk.decode("utf-8", errors="strict"),
                ]
                events.append(event)
                stream.write(json.dumps(event) + "\n")
                stream.flush()
                for scene in ("1 / 4", "2 / 4", "3 / 4", "4 / 4"):
                    if scene in event[2]:
                        print("Captured scene " + scene, flush=True)
            elif process.poll() is not None:
                break
        os.close(master)
        code = process.wait(timeout=10)
        if code:
            raise RuntimeError(f"Demo exited {code}; recording is not a successful demonstration")
        elapsed = time.monotonic() - started
        if elapsed > 88:
            raise RuntimeError(f"Capture took {elapsed:.1f}s; reduce demo pause and record again")
        if elapsed < 75:
            time.sleep(75 - elapsed)  # Leave the actual completed terminal screen visible.
        duration = time.monotonic() - started
        stream.write(json.dumps([round(duration, 4), "o", ""]) + "\n")
    return events, duration, header


def font():
    candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ]
    path = next((p for p in candidates if Path(p).exists()), None)
    if path is None:
        raise RuntimeError("Install a monospace font (Menlo or DejaVu Sans Mono).")
    return ImageFont.truetype(path, 17)


def render(events, duration, destination):
    frames = ROOT / "tmp/demo-frames"
    frames.mkdir(parents=True, exist_ok=True)
    screen, cursor, last = Terminal(), 0, None
    timeline = []
    face = font()
    for tick in range(int(duration * 6) + 1):
        at = min(tick / 6, duration)
        while cursor < len(events) and events[cursor][0] <= at:
            screen.feed(events[cursor][2])
            cursor += 1
        content = screen.screen()
        if content == last:
            continue
        last = content
        frame = Image.new("RGB", (WIDTH, HEIGHT), "#10151f")
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, WIDTH, 40), fill="#202838")
        draw.text(
            (24, 11),
            "Pipeline Copilot  |  Actual terminal capture  |  Model replay",
            font=face,
            fill="#bdc8da",
        )
        for number, line in enumerate(content.split("\n")):
            color = "#d9e2ee"
            if line.startswith(("1 / 4", "2 / 4", "3 / 4", "4 / 4", "PREVIEW")):
                color = "#76baff"
            elif line.startswith(("EXECUTED", "Exact outcomes:", "Successfully exercised")):
                color = "#83d4b0"
            elif line.startswith("REFUSED"):
                color = "#f1bc79"
            draw.text((24, 52 + number * 23), line, font=face, fill=color)
        name = f"frame-{len(timeline):04d}.png"
        frame.save(frames / name)
        timeline.append((at, name))
    concat = ["ffconcat version 1.0"]
    for index, (at, name) in enumerate(timeline):
        end = timeline[index + 1][0] if index + 1 < len(timeline) else duration
        concat.extend([f"file {name}", f"duration {max(end - at, 0.001):.6f}"])
    concat.append(f"file {timeline[-1][1]}")
    playlist = frames / "frames.ffconcat"
    playlist.write_text("\n".join(concat) + "\n")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-safe",
            "1",
            "-i",
            str(playlist),
            "-vf",
            "fps=12",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-t",
            f"{duration:.3f}",
            str(destination),
        ],
        check=True,
    )
    return len(timeline)


def main():
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required for the optional MP4 rendering.")
    directory = ROOT / "artifacts"
    directory.mkdir(exist_ok=True)
    events, duration, header = capture(directory / "demo.cast")
    frames = render(events, duration, directory / "demo.mp4")
    metadata = {
        "kind": "Actual pseudo-terminal capture, rendered from asciicast to H.264",
        "command": header["command"],
        "timestamp": header["timestamp"],
        "duration_seconds": duration,
        "reading_pause_seconds_per_scene": 14,
        "distinct_screen_frames": frames,
        "mode": "replay of genuine recorded model responses",
        "video_sha256": hashlib.sha256((directory / "demo.mp4").read_bytes()).hexdigest(),
        "transcript_sha256": hashlib.sha256((directory / "demo.cast").read_bytes()).hexdigest(),
    }
    (directory / "demo-recording.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Recorded {duration:.2f}s: artifacts/demo.mp4 and artifacts/demo.cast", flush=True)


if __name__ == "__main__":
    main()
