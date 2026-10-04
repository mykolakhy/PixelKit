"""Platform and bundled ImageMagick support, independent of Qt."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def resource_roots() -> list[Path]:
    roots = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            roots.append(Path(sys._MEIPASS) / "pixelkit" / "assets")
            roots.append(Path(sys._MEIPASS))
        # Preserve support for the existing Windows installer layout.
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent / "assets")
    return list(dict.fromkeys(roots))


def resource_path(name: str) -> Path:
    return next((root / name for root in resource_roots() if (root / name).is_file()), resource_roots()[0] / name)


def find_magick() -> str | None:
    name = "magick.exe" if sys.platform == "win32" else "magick"
    candidates = []
    for root in resource_roots():
        candidates.extend((root / "imagemagick" / "bin" / name, root / "imagemagick" / name))
    candidates.append(shutil.which("magick"))
    if sys.platform == "darwin":
        # Finder does not inherit the user's shell PATH.
        candidates.extend(("/opt/homebrew/bin/magick", "/usr/local/bin/magick", "/opt/local/bin/magick"))
    elif sys.platform == "win32":
        candidates.extend((
            r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe",
            r"C:\Program Files\ImageMagick-7.1.1-Q16-HDRI\magick.exe",
        ))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
            return str(candidate)
    return None


def magick_environment(executable: str, environment: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if environment is None else environment)
    if sys.platform != "darwin":
        return env
    # Resolve from the selected executable, including PyInstaller's symlinks
    # between Contents/Resources and Contents/Frameworks.
    executable_path = Path(executable).absolute()
    root = executable_path.parent.parent
    if root.name != "imagemagick":
        return env
    env["MAGICK_HOME"] = str(root)
    configure = sorted((root / "etc").glob("ImageMagick-*"))
    configure.extend(sorted((root / "lib").glob("ImageMagick*/config-*")))
    coders = sorted((root / "lib").glob("ImageMagick*/modules-*/coders"))
    filters = sorted((root / "lib").glob("ImageMagick*/modules-*/filters"))
    for key, paths in (("MAGICK_CONFIGURE_PATH", configure), ("MAGICK_CODER_MODULE_PATH", coders), ("MAGICK_FILTER_MODULE_PATH", filters)):
        if paths:
            env[key] = os.pathsep.join(str(path) for path in paths)
    return env


class ProcessingCancelled(Exception):
    """The caller requested that the active conversion stop."""


def run_magick(command: list[str], **kwargs):
    cancelled = kwargs.pop("cancel_requested", None)
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            kwargs["creationflags"] = flags
    kwargs["env"] = magick_environment(command[0], kwargs.get("env"))
    if cancelled is not None:
        if cancelled():
            raise ProcessingCancelled()
        timeout = kwargs.pop("timeout", None)
        if kwargs.pop("capture_output", False):
            kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + timeout if timeout is not None else None
        with subprocess.Popen(command, **kwargs) as process:
            try:
                while True:
                    if cancelled():
                        raise ProcessingCancelled()
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        stdout, stderr = process.communicate(timeout=0.1)
                        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
    return subprocess.run(command, **kwargs)


def missing_magick_message() -> str:
    if sys.platform == "darwin":
        return "ImageMagick was not found. When running from source, install it with:\n\nbrew install imagemagick\n\nThen restart PixelKit. The packaged macOS app includes ImageMagick."
    return "ImageMagick was not found. Install ImageMagick 7 and add it to PATH, then restart PixelKit."
