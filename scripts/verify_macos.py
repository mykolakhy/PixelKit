"""Check the packaged GUI and image codecs without a shell or Homebrew PATH."""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from pixelkit.runtime import magick_environment


def verify(app: Path) -> None:
    resources = app / "Contents" / "Resources"
    frameworks = app / "Contents" / "Frameworks"
    executable = app / "Contents" / "MacOS" / "PixelKit"
    magick = resources / "imagemagick" / "bin" / "magick"
    assert executable.is_file() and magick.is_file(), "App or ImageMagick executable is missing"
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
