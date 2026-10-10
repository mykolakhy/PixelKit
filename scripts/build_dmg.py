"""Create a branded macOS installer from an existing, signed PixelKit.app."""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WINDOW_SIZE = (680, 440)


def render_background(directory: Path) -> Path:
    """Render the editable SVG at 1x and 2x for dmgbuild's Retina TIFF."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QGuiApplication, QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    application = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(PROJECT / "packaging" / "macos" / "dmg-background.svg"))
    logo = QImage(str(PROJECT / "src" / "pixelkit" / "assets" / "PixelKit.png"))
    if not renderer.isValid() or logo.isNull():
        raise ValueError("Cannot load the installer background or PixelKit logo.")
    background = directory / "background.png"
    for scale in (1, 2):
        image = QImage(WINDOW_SIZE[0] * scale, WINDOW_SIZE[1] * scale, QImage.Format.Format_ARGB32)
        image.fill(0)
        image.setDotsPerMeterX(round(72 * scale / 0.0254))
        image.setDotsPerMeterY(round(72 * scale / 0.0254))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(scale, scale)
        renderer.render(painter, QRectF(0, 0, *WINDOW_SIZE))
        painter.drawImage(QRectF(42, 42, 44, 44), logo)
        painter.end()
        target = background if scale == 1 else directory / "background@2x.png"
        if not image.save(str(target)):
            raise OSError(f"Cannot save installer background: {target}")
    # Keep the Qt application alive until all font and image rendering finishes.
    del application
    return background


def build_dmg(app: Path, output: Path) -> Path:
    if sys.platform != "darwin":
        raise ValueError("Disk images must be built on macOS.")
    app = app.resolve(strict=True)
    output = output.absolute()
    if app.name != "PixelKit.app" or not app.is_dir():
        raise ValueError("Provide an existing PixelKit.app bundle.")
    if output.suffix.lower() != ".dmg" or output.is_symlink() or output.is_dir():
        raise ValueError("The output must be a regular .dmg file path.")
    if output.resolve().is_relative_to(app):
        raise ValueError("The disk image cannot be written inside the app bundle.")
    information = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    icon_name = information.get("CFBundleIconFile", "PixelKit.icns")
    icon = app / "Contents" / "Resources" / icon_name
    if not icon.suffix:
        icon = icon.with_suffix(".icns")
    if not icon.is_file():
        raise ValueError("The app bundle is missing its volume icon.")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)

    import dmgbuild

    copied_app: str | None = None

    def verify_copy(event: dict) -> None:
        # dmgbuild uses ditto to preserve signatures, but does not check its
        # exit status. Verify the copied bundle before sealing the image.
        nonlocal copied_app
        if event.get("type") != "operation::finished":
            return
        if event.get("operation") == "file::add":
            copied_app = event["file"]
        elif event.get("operation") == "extensions::hide":
            if copied_app is None:
                raise ValueError("The app bundle was not copied into the disk image.")
            subprocess.run(["codesign", "--verify", "--deep", "--strict", copied_app], check=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    # Work beside the output so a completed image can replace it atomically.
    # A failed build leaves the existing installer and source bundle intact.
    with tempfile.TemporaryDirectory(prefix=".pixelkit-dmg-", dir=output.parent) as temporary:
        work = Path(temporary)
        background = render_background(work)
        candidate = work / output.name
        dmgbuild.build_dmg(str(candidate), "PixelKit", callback=verify_copy, settings={
            "format": "UDZO",
            "files": [str(app)],
            "symlinks": {"Applications": "/Applications"},
            "icon": str(icon),
            "background": str(background),
            "window_rect": ((180, 180), WINDOW_SIZE),
            "default_view": "icon-view",
            "icon_locations": {"PixelKit.app": (170, 266), "Applications": (510, 266)},
            # Hiding .app adds FinderInfo to the signed bundle and fails strict verification.
            "icon_size": 96,
            "text_size": 13,
            "grid_spacing": 80,
            "arrange_by": None,
            "show_status_bar": False,
            "show_tab_view": False,
            "show_toolbar": False,
            "show_pathbar": False,
            "show_sidebar": False,
            "include_icon_view_settings": True,
            "include_list_view_settings": False,
        })
        subprocess.run(["hdiutil", "verify", str(candidate)], check=True)
        candidate.replace(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True, help="Existing signed PixelKit.app")
    parser.add_argument("--output", type=Path, required=True, help="Destination .dmg")
    args = parser.parse_args()
    print(f"Disk image: {build_dmg(args.app, args.output)}")


if __name__ == "__main__":
    main()
