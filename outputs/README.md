# PixelKit

A modern Qt 6 / PyQt6 graphical interface for ImageMagick CLI on Windows.

## Installation

1. Run `PixelKit-Setup.exe`.
2. The installer will create PixelKit shortcuts on the desktop and in the Start menu.

The packaged installer includes ImageMagick. When running from source, Python, PyQt6, and ImageMagick must be installed separately. You can also launch the app by double-clicking `PixelKit.cmd`.

To run the Qt application directly:

```text
python ImageMagick_Studio_Qt.py
```

## Features

- Support for JPG, PNG, WEBP, GIF, BMP, TIFF, AVIF, and other ImageMagick formats.
- Batch processing of multiple files or all images in a selected folder.
- Resize by width and height, with optional aspect-ratio preservation.
- Resize by the longest side for mixed landscape and portrait images.
- Adjustable compression quality.
- Convert to JPG, PNG, WEBP, AVIF, GIF, BMP, TIFF, and more.
- Remove EXIF and other image metadata.
- Built-in input image preview.
- Select a custom output folder.

For batch processing, use `Multiple files` or `Folder`. Results automatically receive the `_optimized` suffix and are saved to the selected output folder.

The Qt version is optimized for Windows HiDPI scaling and supports drag-and-drop image files.
