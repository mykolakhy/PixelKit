"""Bounded quality search that publishes only files below the requested limit."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pixelkit.runtime import run_magick

TARGET_FORMATS = {"jpg", "jpeg", "webp", "avif"}


def compress_to_size(command: list[str], output: Path, limit: int, cancelled) -> int:
    if output.suffix.lower().lstrip(".") not in TARGET_FORMATS:
        raise ValueError("File-size limits support JPG, WEBP and AVIF. Choose one of these output formats.")
    if type(limit) is not int or limit < 1:
        raise ValueError("Enter a positive file-size limit.")
    quality_index = command.index("-quality") + 1
    maximum = int(command[quality_index])
    deadline = time.monotonic() + 300
    trials = output.parent / "quality-trials"
    trials.mkdir(exist_ok=True)

    def attempt(quality: int) -> Path:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, 300)
        trial = trials / f"quality-{quality}{output.suffix}"
        args = list(command)
        args[quality_index] = str(quality)
        args[-1] = str(trial)
        if output.suffix.lower() == ".webp":
            args[-1:-1] = ["-define", "webp:lossless=false"]
        result = run_magick(args, capture_output=True, text=True, timeout=remaining, cancel_requested=cancelled)
        if result.returncode or not trial.is_file():
            raise OSError(result.stderr.strip() or result.stdout.strip() or "ImageMagick did not create the expected output.")
        return trial

    best = attempt(maximum)
    if best.stat().st_size <= limit:
        best.replace(output)
        return maximum
    best = attempt(1)
    if best.stat().st_size > limit:
        raise ValueError(f"Could not reach the requested limit of {limit / 1024:.1f} KiB. At quality 1 the file is {best.stat().st_size / 1024:.1f} KiB. Try smaller dimensions or a different format. No oversized output was saved.")
    low, high = 1, maximum
    while high - low > 1:
        middle = (low + high) // 2
        trial = attempt(middle)
        if trial.stat().st_size <= limit:
            best, low = trial, middle
        else:
            high = middle
    best.replace(output)
    return low
