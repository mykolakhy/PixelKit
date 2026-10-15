"""Check the packaged GUI, image codecs and video runtime without Homebrew PATH."""
from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import tempfile
from pathlib import Path

from pixelkit.runtime import magick_environment


def verify(app: Path) -> None:
    resources = app / "Contents" / "Resources"
    frameworks = app / "Contents" / "Frameworks"
    executable = app / "Contents" / "MacOS" / "PixelKit"
    magick = resources / "imagemagick" / "bin" / "magick"
    ffmpeg = resources / "ffmpeg" / "bin" / "ffmpeg"
    ffprobe = resources / "ffmpeg" / "bin" / "ffprobe"
    assert executable.is_file() and magick.is_file(), "App or ImageMagick executable is missing"
    assert ffmpeg.is_file() and ffprobe.is_file(), "FFmpeg or FFprobe executable is missing"
    with (app / "Contents" / "Info.plist").open("rb") as source:
        metadata = plistlib.load(source)
    extensions = {extension for document in metadata.get("CFBundleDocumentTypes", []) for extension in document.get("CFBundleTypeExtensions", [])}
    assert {"mp4", "mov", "m4v"}.issubset(extensions), "Video document registration is incomplete"
    for name in ("PixelKit.png", "PixelKit.ico", "check.svg", "chevron-down.svg", "chevron-up.svg"):
        assert (resources / "pixelkit" / "assets" / name).is_file(), f"Application resource is missing: {name}"
    # Check every bundled Mach-O dependency. Absolute Homebrew paths would make
    # a build appear functional locally but break on another person's Mac.
    for binary in frameworks.rglob("*"):
        if not binary.is_file() or binary.is_symlink():
            continue
        description = subprocess.check_output(["file", "-b", str(binary)], text=True)
        if "Mach-O" in description:
            links = subprocess.check_output(["otool", "-L", str(binary)], text=True)
            for line in links.splitlines()[1:]:
                link = line.strip().split(" (", 1)[0]
                assert not link.startswith("/") or link.startswith(("/usr/lib/", "/System/Library/")), f"Nonportable dependency in {binary}: {link}"
    with tempfile.TemporaryDirectory(prefix="pixelkit_bundle_check_") as directory:
        root = Path(directory)
        env = magick_environment(str(magick), {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root), "TMPDIR": str(root), "QT_QPA_PLATFORM": "offscreen"})

        def run(*arguments: str) -> str:
            result = subprocess.run([str(magick), *arguments], env=env, text=True, capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f"Bundled ImageMagick failed: {result.stderr.strip()}")
            return result.stdout.strip()

        print(run("-version").splitlines()[0])
        source = root / "input with spaces.png"
        run("-size", "96x64", "gradient:#7c5cff-#32d6c8", str(source))
        for extension in ("jpg", "png", "webp", "avif", "gif", "bmp", "tiff", "heic", "ico"):
            output = root / f"output.{extension}"
            run(str(source), "-resize", "48x48", "-strip", "-quality", "82", str(output))
            dimensions = run("identify", "-format", "%wx%h", str(output))
            assert dimensions == "48x32", f"Unexpected {extension} dimensions: {dimensions}"
            print(f"Verified {extension.upper()} encoding and decoding")
        movie = root / "video output with spaces.mp4"
        def video_run(binary: Path, *arguments: str) -> str:
            result = subprocess.run([str(binary), *arguments], env=env, text=True, capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f"Bundled {binary.name} failed: {result.stderr.strip()}")
            return result.stdout

        print(video_run(ffmpeg, "-version").splitlines()[0])
        video_run(ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-loop", "1", "-framerate", "10", "-i", str(source),
                  "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.3",
                  "-vf", "scale=48:32", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(movie))
        probe = json.loads(video_run(ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(movie)))
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
        assert (video["codec_name"], video["width"], video["height"]) == ("h264", 48, 32), "Unexpected bundled video encoding"
        assert audio["codec_name"] == "aac", "Unexpected bundled audio encoding"
        assert float(probe["format"]["duration"]) > 0, "Bundled movie has no duration"
        video_run(ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(movie), "-f", "null", "-")
        print("Verified H.264/AAC video encoding, decoding and FFprobe inspection")
        # Check the GUI starts with only the packaged Python, Qt and resources.
        with (root / "gui.log").open("w+") as log:
            process = subprocess.Popen([str(executable)], env=env, cwd=root, stdout=log, stderr=log)
            try:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print("Verified packaged Qt startup with a minimal environment")
                else:
                    log.seek(0)
                    raise RuntimeError(f"GUI exited during startup ({process.returncode}): {log.read()}")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    verify(parser.parse_args().app.resolve())
