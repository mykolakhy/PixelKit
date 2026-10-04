# PixelKit

A Windows and macOS desktop app for local batch image resizing, compression, and format conversion, powered by ImageMagick.

## Install the app

Application bundles and installers belong in `dist/`, outside version control.

- **macOS:** build a disk image using the [macOS instructions](packaging/macos/README.md), or download the matching artifact from a completed **macOS app** GitHub Actions run. Open the DMG and drag PixelKit into Applications.
- **Windows:** build the app and installer from the current source on Windows using the [Windows packaging instructions](packaging/windows/README.md).

The packaged macOS app includes Python, Qt, ImageMagick, and its codecs. Apple Silicon and Intel builds are separate; the build host's macOS major version is the minimum supported version. See the packaging instructions for signing and distribution.

## Features

- Batch processing of multiple files or entire folders.
- Resize by width and height, with optional aspect-ratio locking.
- Resize by the longest side for mixed landscape and portrait images.
- Image compression with adjustable quality.
- Convert between JPG, PNG, WEBP, AVIF, GIF, BMP, TIFF, and other formats supported by ImageMagick.
- Remove EXIF and other image metadata.
- Drag-and-drop support for image files and folders.
- Modern dark interface with HiDPI / Retina support.
- On macOS: native menu shortcuts and image opening through Finder's **Open With** or the Dock icon.
- All processing is performed locally on your computer.
- Built-in processing presets and your own saved presets, available after restarting the app.

## Presets

Choose a preset above the image settings to apply it to every input image:

| Preset | Format | Resize | Quality |
| --- | --- | --- | --- |
| For website | WEBP | Longest side 1920 px | 80 |
| For email | JPG | Longest side 1600 px | 75 |
| PNG · original size | PNG | Original dimensions | 100 |

The built-in presets remove metadata. Adjust any processing setting, then click **Save preset…** and give it a name to save your own format, resize settings, quality, metadata preference, and JPEG background. Saved presets are stored locally in your user preferences on both macOS and Windows. Saving under an existing name asks before replacing it; **Delete preset** removes a custom preset after confirmation.

Editing settings switches the selector to **Custom settings**. Presets keep the current input files and export folder; for a single image, the output filename's extension follows the preset's format. They do not store image paths or start processing automatically. Preset controls are disabled while a batch is running.

## Run from source

Use Python 3.10 or newer and ImageMagick 7. Install the project once in a virtual environment, then launch it as a package.

### macOS

```sh
brew install python@3.12 imagemagick
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pixelkit
```

### Windows

Install ImageMagick 7 with its executable available in PATH, then run:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m pixelkit
```

Installing the package also provides the `pixelkit` command inside the virtual environment.

## Repository layout

```text
src/pixelkit/       Application package and bundled icons
  app.py           Main window and batch processing
  presets.py       Built-in presets and saved preferences
  runtime.py       Platform resources and ImageMagick discovery
  widgets.py       Shared dropdown widgets
  assets/          Logo, Windows icon, and SVG controls
packaging/         Platform-specific build configuration and installer
scripts/           macOS builder and bundle verification
tests/             Application, preset, and runtime checks
.github/workflows/ macOS build automation
pyproject.toml     Dependencies, package metadata, and launch command
```

Dependencies are declared in `pyproject.toml`; the version is declared in `src/pixelkit/__init__.py`. `build/` contains disposable build intermediates; `dist/` contains local deliverables. Both are ignored by Git. Close any app launched from `dist/` before removing or rebuilding that bundle.

## Development and validation

See [macOS packaging](packaging/macos/README.md) and [Windows packaging](packaging/windows/README.md). Install with `pip install -e '.[build]'` to include the optional PyInstaller dependency for native builds.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover presets, file protection, batch conversion, Finder events, and resource discovery on Windows and macOS. ImageMagick is required for the real-image integration check. Every macOS build also verifies embedded signatures, library paths, image codecs, packaged assets, and GUI startup.

## Security

See [SECURITY.md](SECURITY.md) for the vulnerability reporting policy.
