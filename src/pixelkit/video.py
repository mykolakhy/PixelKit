"""Local SDR video compression with FFmpeg and cancellable batch processing."""
from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from pixelkit.report import BatchReport, FileResult
from pixelkit.runtime import ProcessingCancelled, resource_roots


VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v"})
# CRF controls quality, independently of the encoder's speed preset.
VIDEO_PRESETS = {
    "high": (20, "medium", 192),
    "balanced": (24, "medium", 128),
    "small": (28, "medium", 96),
}
MP4_AUDIO_CODECS = frozenset({"aac", "mp3", "ac3", "eac3", "alac"})
SPATIAL_AUDIO_NOTE = "Standard audio was retained. Apple spatial audio (APAC) is not included in the MP4."
MAX_TARGET_ATTEMPTS = 5


@dataclass(frozen=True)
class VideoSettings:
    preset: str = "balanced"
    max_height: int = 0
    audio: str = "compress"
    target_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.preset, str) or self.preset not in VIDEO_PRESETS:
            raise ValueError("Choose High quality, Balanced, or Smallest size.")
        if type(self.max_height) is not int or self.max_height not in {0, 1080, 720}:
            raise ValueError("Choose Original, 1080p, or 720p for the video resolution.")
        if not isinstance(self.audio, str) or self.audio not in {"keep", "compress", "remove"}:
            raise ValueError("Choose Keep, Compress, or Remove for audio.")
        if self.target_bytes is not None and (type(self.target_bytes) is not int or self.target_bytes < 1):
            raise ValueError("Enter a positive video file size limit.")


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    stream_index: int
    width: int
    height: int
    audio_codecs: tuple[str, ...]


def _find_tool(name: str) -> str | None:
    executable = name + (".exe" if sys.platform == "win32" else "")
    candidates = []
    for root in resource_roots():
        candidates.extend((root / "ffmpeg" / "bin" / executable, root / "ffmpeg" / executable))
    candidates.append(shutil.which(name))
    if sys.platform == "darwin":
        # Finder does not inherit the user's shell PATH.
        candidates.extend(Path(prefix) / executable for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
            return str(candidate)
    return None


def find_ffmpeg() -> str | None:
    return _find_tool("ffmpeg")


def find_ffprobe() -> str | None:
    return _find_tool("ffprobe")


def _process_options() -> dict:
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _run_captured(command: list[str], cancelled: Callable[[], bool], timeout: float = 30) -> subprocess.CompletedProcess:
    """Probe/capability queries are bounded; video encoding itself has no time limit."""
    if cancelled():
        raise ProcessingCancelled()
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **_process_options()) as process:
        try:
            while True:
                if cancelled():
                    raise ProcessingCancelled()
                if time.monotonic() >= deadline:
                    raise ValueError("Reading video information took too long. The input may be damaged.")
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            _stop_process(process)


def probe_video(source: Path, ffprobe: str, cancelled: Callable[[], bool] = lambda: False) -> VideoInfo:
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("Supported video files are MP4, MOV, and M4V.")
    result = _run_captured([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source.absolute())], cancelled)
    if result.returncode:
        raise ValueError("Cannot read this video. " + (result.stderr.strip() or "The file may be damaged or unsupported."))
    try:
        metadata = json.loads(result.stdout)
        streams = metadata["streams"]
        if not isinstance(streams, list) or not all(isinstance(stream, dict) for stream in streams):
            raise ValueError()
        video = next(stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"))
        width, height = int(video["width"]), int(video["height"])
        stream_index = int(video["index"])
        format_names = set(metadata["format"].get("format_name", "").split(","))
        durations = [float(value) for value in (video.get("duration"), metadata["format"].get("duration")) if value not in (None, "N/A")]
        duration = max(durations)
        if width < 2 or height < 2 or stream_index < 0 or any(not math.isfinite(value) for value in durations) or duration <= 0 or not format_names.intersection({"mov", "mp4", "m4v"}):
            raise ValueError()
    except (KeyError, TypeError, ValueError, StopIteration, AttributeError):
        raise ValueError("This file does not contain a readable MP4, MOV, or M4V video with a known duration.") from None
    side_data = " ".join(str(item.get("side_data_type", "")) for item in video.get("side_data_list", []) if isinstance(item, dict)).lower()
    if video.get("color_transfer") in {"smpte2084", "arib-std-b67"} or video.get("color_primaries") in {"bt2020", "smpte431", "smpte432"} or any(marker in side_data for marker in ("mastering display", "content light", "dovi", "dolby")):
        raise ValueError("HDR and wide-gamut videos are not supported yet. Use an SDR version to preserve its colours.")
    pixel_format = str(video.get("pix_fmt", "")).lower()
    # H.264/yuv420p cannot preserve transparency. Reject formats with an alpha
    # channel, including paletted inputs which may carry alpha in their palette.
    if pixel_format == "pal8" or pixel_format.startswith(("rgba", "argb", "bgra", "abgr", "yuva", "gbrap", "ya", "ayuv", "vuya", "uyva")):
        raise ValueError("Video transparency is not supported yet. Export an opaque video before compressing it to MP4.")
    # Older ffprobe versions identify Apple's spatial track only by its MOV
    # tag. Marian's unrelated 'apac' decoder cannot decode this Apple format.
    codecs = tuple("apple_apac" if stream.get("codec_tag_string") == "apac" else str(stream.get("codec_name", "")) for stream in streams if stream.get("codec_type") == "audio")
    return VideoInfo(duration, stream_index, width, height, codecs)


def video_command(ffmpeg: str, source: Path, output: Path, settings: VideoSettings, info: VideoInfo, *, video_bitrate: int | None = None, pass_number: int | None = None, pass_log: Path | None = None) -> list[str]:
    crf, speed, bitrate = VIDEO_PRESETS[settings.preset]
    factor = f"min(1,{settings.max_height}/ih)" if settings.max_height else "1"
    # FFmpeg applies display rotation before this filter. Square pixels preserve
    # the displayed aspect ratio, including anamorphic source material.
    scale = f"scale=w='max(2,trunc(iw*sar*{factor}/2)*2)':h='max(2,trunc(ih*{factor}/2)*2)':flags=lanczos,setsar=1"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-xerror", "-progress", "pipe:1", "-nostats", "-i", str(source.absolute()), "-map", f"0:{info.stream_index}", "-vf", scale, "-c:v", "libx264", "-preset", speed]
    command.extend(("-crf", str(crf)) if video_bitrate is None else ("-b:v", str(video_bitrate)))
    # Screen recordings can have tightly spaced frames followed by long gaps.
    # Keep the filter's timestamp precision instead of rounding to a nominal
    # frame rate. Avoid B-frame reordering, which can shorten the MP4 track's
    # advertised duration for these irregular timestamps.
    command.extend(("-pix_fmt", "yuv420p", "-fps_mode", "passthrough", "-enc_time_base:v", "filter", "-bf:v", "0", "-map_metadata", "-1", "-map_chapters", "-1", "-metadata:s:v:0", "rotate=0"))
    if pass_number is not None:
        if pass_number not in {1, 2} or video_bitrate is None or pass_log is None:
            raise ValueError("Two-pass encoding requires a bitrate and private pass log.")
        command.extend(("-pass:v", str(pass_number), "-passlogfile", str(pass_log.absolute())))
        if pass_number == 1:
            command.extend(("-an", "-f", "null", os.devnull))
            return command
    if settings.audio == "remove":
        command.append("-an")
    elif info.audio_codecs:
        # iPhone MOV files can contain both standard AAC and an additional
        # Apple spatial track without an FFmpeg decoder. Retain the standard
        # tracks explicitly, preserving their input order and output indexes.
        tracks = [(index, codec) for index, codec in enumerate(info.audio_codecs) if codec != "apple_apac"]
        if not tracks:
            raise ValueError("This video has only Apple spatial audio (APAC), which PixelKit cannot convert yet. Choose Remove audio, or export a version with standard AAC audio. No output was saved.")
        for index, _ in tracks:
            command.extend(("-map", f"0:a:{index}"))
        if settings.audio == "compress":
            command.extend(("-c:a", "aac", "-b:a", f"{bitrate}k"))
        else:
            for index, (_, codec) in enumerate(tracks):
                command.extend((f"-c:a:{index}", "copy" if codec in MP4_AUDIO_CODECS else "aac"))
                if codec not in MP4_AUDIO_CODECS:
                    command.extend((f"-b:a:{index}", "192k"))
    command.extend(("-movflags", "+faststart", "-f", "mp4", str(output.absolute())))
    return command


def encode_video(command: list[str], duration: float, cancelled: Callable[[], bool], progress: Callable[[int], None]) -> None:
    """Drain progress asynchronously so cancellation also works between updates."""
    if cancelled():
        raise ProcessingCancelled()
    updates = queue.Queue()
    with tempfile.TemporaryFile(mode="w+b") as errors:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace", **_process_options()) as process:
            def read_progress() -> None:
                try:
                    for line in process.stdout:
                        updates.put(line)
                finally:
                    updates.put(None)

            reader = Thread(target=read_progress, daemon=True)
            reader.start()
            previous = -1
            try:
                while True:
                    if cancelled():
                        raise ProcessingCancelled()
                    try:
                        line = updates.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    key, separator, value = line.strip().partition("=")
                    if separator and key == "out_time_us":
                        try:
                            percent = max(previous, min(99, max(0, int(int(value) / (duration * 1000000) * 100))))
                        except ValueError:
                            continue
                        if percent != previous:
                            progress(percent)
                            previous = percent
                # The progress pipe may close before the muxer finishes moving
                # the MP4 header; keep checking cancellation until process exit.
                while process.poll() is None:
                    if cancelled():
                        raise ProcessingCancelled()
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        continue
                if cancelled():
                    raise ProcessingCancelled()
                if process.returncode:
                    # Retain the first cause as well as the final failure for
                    # the report's copy/save error-log actions.
                    errors.seek(0)
                    message = errors.read().decode("utf-8", "replace").strip()
                    raise ValueError("Could not compress this video. " + (message or "FFmpeg returned an unknown error."))
            finally:
                _stop_process(process)
                reader.join(timeout=2)


def _encoded_size(output: Path) -> int:
    if not output.is_file() or output.stat().st_size == 0:
        raise ValueError("FFmpeg did not produce a complete video. No output was saved.")
    return output.stat().st_size


def _limit_error(target: int) -> ValueError:
    return ValueError(f"Cannot fit this video within {target / 1000000:g} MB with the selected resolution and audio. Try a larger limit, a lower resolution, or Compress/Remove audio. No output was saved.")


def encode_to_target(ffmpeg: str, source: Path, output: Path, settings: VideoSettings, info: VideoInfo, cancelled: Callable[[], bool], progress: Callable[[int], None]) -> None:
    """Keep the chosen quality when it fits; otherwise budget audio and video.

    All trials and pass logs live beside output in the worker's private directory.
    A bitrate is only an estimate: publication always checks the actual MP4 size.
    """
    target = settings.target_bytes
    if target is None:
        encode_video(video_command(ffmpeg, source, output, settings, info), info.duration, cancelled, progress)
        return
    encode_video(video_command(ffmpeg, source, output, settings, info), info.duration, cancelled, lambda value: progress(value // 4))
    if cancelled():
        raise ProcessingCancelled()
    if _encoded_size(output) <= target:
        return

    audio_size = 0
    if settings.audio != "remove" and info.audio_codecs:
        # Measure the audio we will actually retain, including every track and
        # variable-bitrate/copied streams without reliable bitrate metadata.
        audio_dir = Path(tempfile.mkdtemp(prefix="audio-", dir=output.parent))
        audio_output = audio_dir / "budget.mp4"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-xerror", "-progress", "pipe:1", "-nostats", "-i", str(output.absolute()), "-map", "0:a", "-vn", "-c:a", "copy", "-map_metadata", "-1", "-map_chapters", "-1", "-f", "mp4", str(audio_output.absolute())]
        encode_video(command, info.duration, cancelled, lambda value: None)
        audio_size = _encoded_size(audio_output)
    if audio_size >= target:
        raise _limit_error(target)
    # The reserve is only an estimate. Audio already includes its MP4 index,
    # so even a near-exhausted estimated budget may fit at the minimum bitrate.
    reserve = min(max(4096, math.ceil(target * 0.03)), max(1, target // 4))
    bitrate = max(1000, math.floor((target - audio_size - reserve) * 8 / info.duration))
    for attempt in range(MAX_TARGET_ATTEMPTS):
        if cancelled():
            raise ProcessingCancelled()
        pass_log = output.parent / f"pass-{attempt}"
        for pass_number in (1, 2):
            stage = attempt * 2 + pass_number - 1
            command = video_command(ffmpeg, source, output, settings, info, video_bitrate=bitrate, pass_number=pass_number, pass_log=pass_log)
            encode_video(command, info.duration, cancelled, lambda value, stage=stage: progress(25 + (stage * 100 + value) * 74 // (MAX_TARGET_ATTEMPTS * 200)))
            if cancelled():
                raise ProcessingCancelled()
        size = _encoded_size(output)
        if size <= target:
            return
        if bitrate == 1000:
            break
        # Reduce proportionally to observed bytes, keeping the measured audio
        # fixed. Subtracting the reserve again can drive short clips below the
        # encoder's feasible bitrate even when a fitting result is possible.
        ratio = (target - audio_size) / max(1, size - audio_size)
        bitrate = max(1000, math.floor(bitrate * ratio * 0.95))
    raise _limit_error(target)


class VideoWorker(QThread):
    progress = pyqtSignal(int, int, str)
    encoding_progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def __init__(self, jobs: list[tuple[Path, Path]], output_dir: Path, settings: VideoSettings) -> None:
        super().__init__()
        self.jobs = [(Path(source), Path(output)) for source, output in jobs]
        self.output_dir = Path(output_dir)
        self.settings = settings
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        files = []
        ffmpeg, ffprobe = find_ffmpeg(), find_ffprobe()
        tool_error = None
        tools_checked = False
        processing_settings = (
            ("Quality preset", self.settings.preset),
            ("Resolution", f"Up to {self.settings.max_height}p" if self.settings.max_height else "Original resolution"),
            ("Audio", self.settings.audio),
        )
        source_paths = {source.resolve() for source, _ in self.jobs}
        # Default macOS and Windows volumes are case-insensitive. Conservatively
        # reject case-only duplicate destinations on every platform.
        destination_counts = Counter(str(output.resolve()).casefold() for _, output in self.jobs)
        for index, (source, output) in enumerate(self.jobs, start=1):
            if self.cancel_event.is_set():
                files.append(FileResult(source, output, None, None, "Not processed because the batch was cancelled.", "Skipped", media_type="video", target_bytes=self.settings.target_bytes, processing_settings=processing_settings))
                continue
            started = time.monotonic()
            before = after = None
            warnings = ()
            error = stopped = None
            temporary_dir = None
            self.encoding_progress.emit(0, source.name)
            try:
                before = source.stat().st_size
                if not source.is_file():
                    raise ValueError("The input is not a video file.")
                if output.suffix.lower() != ".mp4":
                    raise ValueError("Compressed videos must be saved as MP4.")
                if output.resolve() in source_paths or (output.exists() and any(other.is_file() and output.samefile(other) for other, _ in self.jobs)):
                    raise ValueError("The output would replace an original video. Choose another folder or filename.")
                if destination_counts[str(output.resolve()).casefold()] > 1:
                    raise ValueError("Multiple videos have the same output path. Choose unique output filenames.")
                if not tools_checked:
                    if not ffmpeg or not ffprobe:
                        tool_error = "FFmpeg and ffprobe were not found. Install FFmpeg when running from source, then restart PixelKit. Packaged apps include both tools."
                    else:
                        encoders = _run_captured([ffmpeg, "-hide_banner", "-encoders"], self.cancel_event.is_set)
                        if encoders.returncode or not any(len(parts := line.split()) > 1 and parts[1] == "libx264" for line in encoders.stdout.splitlines()):
                            tool_error = "This FFmpeg build does not include the libx264 encoder required for video compression."
                    tools_checked = True
                if tool_error:
                    raise ValueError(tool_error)
                info = probe_video(source, ffprobe, self.cancel_event.is_set)
                temporary_dir = Path(tempfile.mkdtemp(prefix=".pixelkit-video-", dir=output.parent))
                temporary = temporary_dir / output.name
                encode_to_target(ffmpeg, source, temporary, self.settings, info, self.cancel_event.is_set, lambda percent: self.encoding_progress.emit(percent, source.name))
                if self.cancel_event.is_set():
                    raise ProcessingCancelled()
                encoded_size = _encoded_size(temporary)
                if self.settings.target_bytes is not None and encoded_size > self.settings.target_bytes:
                    raise _limit_error(self.settings.target_bytes)
                if encoded_size >= before:
                    raise ValueError("No size reduction with these settings. The original may already be optimized; try Smallest size or a lower resolution. No output was saved.")
                temporary.replace(output)
                after = encoded_size
                if self.settings.audio != "remove" and "apple_apac" in info.audio_codecs:
                    warnings = (SPATIAL_AUDIO_NOTE,)
                self.encoding_progress.emit(100, source.name)
            except ProcessingCancelled:
                error = "Processing cancelled. No partial output was saved."
                stopped = "Cancelled"
            except (OSError, ValueError) as exc:
                error = str(exc)
            finally:
                if temporary_dir is not None:
                    try:
                        shutil.rmtree(temporary_dir)
                    except OSError as exc:
                        error = f"{error or 'Compression completed.'}\nCould not remove temporary files at {temporary_dir}: {exc}"
            files.append(FileResult(source, output, before, after, error, stopped, media_type="video", elapsed_seconds=time.monotonic() - started, target_bytes=self.settings.target_bytes, processing_settings=processing_settings, warnings=warnings))
            self.progress.emit(index, len(self.jobs), source.name)
        self.finished.emit(BatchReport(tuple(files), self.output_dir, any(file.stopped for file in files), retry_settings=self.settings))
