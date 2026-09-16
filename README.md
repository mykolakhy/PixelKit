# PixelKit

A modern Windows desktop GUI for batch image resizing, compression, and format conversion powered by ImageMagick.

## Quick start

1. Download [`outputs/PixelKit-Setup.exe`](outputs/PixelKit-Setup.exe).
2. Run the installer.
3. PixelKit will create shortcuts on the desktop and in the Start menu.

The installer includes the required ImageMagick runtime, so no separate ImageMagick installation is needed for the packaged app.

## Features

- Batch processing of multiple files or entire folders.
- Resize by width and height, with optional aspect-ratio locking.
- Resize by the longest side for mixed landscape and portrait images.
- Image compression with adjustable quality.
- Convert between JPG, PNG, WEBP, AVIF, GIF, BMP, TIFF, and other formats supported by ImageMagick.
- Remove EXIF and other image metadata.
- Built-in preview and drag-and-drop support.
- Modern dark interface optimized for Windows HiDPI scaling.
- All processing is performed locally on your computer.

## Run from source

Install Python and PyQt6, then run:

```powershell
python -m pip install PyQt6
python outputs/ImageMagick_Studio_Qt.py
```

ImageMagick must be installed and available in `PATH` when running from source.

## Project files

- `outputs/PixelKit-Setup.exe` — Windows installer.
- `outputs/ImageMagick_Studio_Qt.py` — main Qt application.
- `outputs/PixelKit_Setup.py` — installer source.
- `outputs/PixelKit.ico` and `outputs/PixelKit.png` — application icons.

For the complete source-build notes, see [`outputs/README.md`](outputs/README.md).
