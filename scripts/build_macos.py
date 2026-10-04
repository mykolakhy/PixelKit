"""Build a standalone PixelKit.app and an architecture-specific disk image."""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pixelkit import __version__
from pixelkit.runtime import find_magick

PROJECT = Path(__file__).resolve().parents[1]


def run(command: list[str], **kwargs) -> None:
    subprocess.run(command, check=True, **kwargs)


def make_icon(work: Path) -> Path:
    iconset = work / "PixelKit.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            pixels = str(size * scale)
            run(["sips", "-z", pixels, pixels, str(PROJECT / "src" / "pixelkit" / "assets" / "PixelKit.png"), "--out", str(iconset / f"icon_{size}x{size}{suffix}.png")], stdout=subprocess.DEVNULL)
    icon = work / "PixelKit.icns"
    run(["iconutil", "-c", "icns", str(iconset), "-o", str(icon)])
    return icon


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--magick-prefix", type=Path, help="ImageMagick installation prefix (normally detected automatically)")
    parser.add_argument("--no-dmg", action="store_true", help="Build only the app bundle")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "dist" / "macos", help="Directory for architecture-specific builds")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("macOS builds must run on a Mac.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("Use a numeric version such as 1.0.0.")
    arch = platform.machine()
    if arch not in {"arm64", "x86_64"}:
        parser.error(f"Unsupported architecture: {arch}")
    magick = find_magick()
    prefix = None
    if args.magick_prefix:
        prefix = args.magick_prefix.resolve()
    elif magick:
        prefix = Path(magick).resolve().parent.parent
    if prefix is None or not (prefix / "bin" / "magick").is_file() or not (prefix / "etc").is_dir():
        parser.error("Install ImageMagick 7 with 'brew install imagemagick', or pass --magick-prefix.")
    # Fail early if the runtime does not contain the target architecture.
    architectures = subprocess.check_output(["lipo", "-archs", str(prefix / "bin" / "magick")], text=True).split()
    if arch not in architectures:
        parser.error(f"ImageMagick has {architectures}, but Python is running as {arch}.")
    work = PROJECT / "build" / "macos" / arch
    dist = args.output_dir.resolve() / arch
    work.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    icon = make_icon(work)
    minimum_version = platform.mac_ver()[0].split(".")[0] + ".0"
    env = dict(os.environ, PIXELKIT_MAGICK_PREFIX=str(prefix), PIXELKIT_MACOS_ICON=str(icon), PIXELKIT_VERSION=args.version, PIXELKIT_ARCH=arch, PIXELKIT_MACOS_MIN_VERSION=minimum_version, PYINSTALLER_CONFIG_DIR=str(work / "cache"))
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(dist), "--workpath", str(work / "pyinstaller"), str(PROJECT / "packaging" / "macos" / "PixelKit.spec")], env=env, cwd=PROJECT)
    app = dist / "PixelKit.app"
    # Validate all embedded signatures before making the disk image.
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    # BUNDLE contains its own collection. Do not keep a second copy of the app.
    shutil.rmtree(dist / "PixelKit")
    run([sys.executable, str(PROJECT / "scripts" / "verify_macos.py"), str(app)])
    if not args.no_dmg:
        staging = work / "dmg"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        run(["ditto", str(app), str(staging / app.name)])
        (staging / "Applications").symlink_to("/Applications")
        dmg = dist / f"PixelKit-{args.version}-macOS-{arch}.dmg"
        try:
            run(["hdiutil", "create", "-volname", "PixelKit", "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(dmg)])
        finally:
            shutil.rmtree(staging)
        print(f"Disk image: {dmg}")
    print(f"Application: {app}")


if __name__ == "__main__":
    main()
