# PixelKit

A Windows and macOS desktop app for local batch image resizing, compression, format conversion, and video compression, powered by ImageMagick and FFmpeg.

## Install the app

Application bundles and installers belong in `dist/`, outside version control.

- **macOS:** download the Apple Silicon or Intel DMG from [GitHub Releases](https://github.com/mykolakhy/PixelKit/releases). Open the DMG and drag PixelKit into Applications. Development builds remain available as artifacts of completed **macOS app** GitHub Actions runs; the [macOS instructions](packaging/macos/README.md) explain building from source.
- **Windows:** build the app and installer from the current source on Windows using the [Windows packaging instructions](packaging/windows/README.md).

The packaged macOS app includes Python, Qt, ImageMagick, FFmpeg/FFprobe, and their codecs. Apple Silicon and Intel builds are separate; the build host's macOS major version is the minimum supported version. See the packaging instructions for signing and distribution.

## Features

- Batch processing of multiple files or entire folders.
- Resize by width and height, with optional aspect-ratio locking.
- Resize by the longest side for mixed landscape and portrait images.
- Image compression with adjustable quality.
- Convert between JPG, PNG, WEBP, AVIF, GIF, BMP, TIFF, and other formats supported by ImageMagick.
- Remove EXIF and other image metadata.
- Drop images, videos or folders anywhere in the workspace. PixelKit switches to **Images** or **Video** for a single media type, appends files without clearing either queue, and ignores duplicates. A mixed image/video drop adds each type to its own queue and keeps the current tab. Folders contribute their immediate files; nested folders are skipped. Drops are disabled while processing.
- Select an input image and use **Remove** (or **Delete / Backspace** while the list has focus) to remove only that image from the queue. **Clear all** empties the queue; original files stay on disk.
- Modern dark interface with HiDPI / Retina support.
- **Help → Check for updates…** checks the latest published stable GitHub release when you ask. It shows the installed and available versions and offers the matching macOS installer, or the release page when an installer for your system is unavailable. Downloads open in your browser; installation stays manual. There are no automatic startup checks. Offline, timeout and GitHub rate-limit errors can be retried without changing your files or settings.
- Two short getting-started screens fill the main window on the first ordinary launch and introduce image/video processing and the add → settings → save workflow. Skip them at any time or reopen them through **Help → Getting started…**. **Get started** returns to the workspace without opening a file picker or starting processing; existing files and settings are kept. Opening a file through Finder or the command line takes priority over the introduction.
- On macOS: native menu shortcuts and image opening through Finder's **Open With** or the Dock icon.
- All processing is performed locally on your computer.
- Built-in processing presets and your own saved presets, available after restarting the app.
- Compression report after each batch: before/after sizes, savings, per-file formats and errors, and an output-folder shortcut. Totals include successful files only; larger outputs are shown explicitly.
- **Retry failed** in image and video reports returns failed originals to the queue with that run's settings and output location. Review or adjust the settings, then start processing explicitly. Successful, cancelled and skipped files are excluded. Missing originals must be restored before retrying; replacing an unrelated current queue asks for confirmation. Preparing a retry keeps the previous report available.
- Select a failed image or video in the report, then use **Copy error log** or **Save error log…** to share its full error with PixelKit version and input/output paths. Logs are saved locally as UTF-8 text, including original diagnostics even when the report shows a shorter explanation.
- Prepare a bug report through **Help → Report a bug…**, the header button, or a selected failed row in the compression report. Describe the steps and actual/expected result, review or edit the automatic technical details, then check **Preview**. Automatic details hide personal paths and file names; a failure includes the settings captured for that processing run. **Continue on GitHub** opens a draft for you to publish with your GitHub account. Reports there are public; media and screenshots are attached manually. Long reports offer **Copy report & open GitHub** so the full text can be pasted without truncation. **Copy report** and **Save report…** work offline. Drafts stay in memory when reopened during the current app session; save a report before quitting if you need to keep it.
- Cancel an active batch: stop the current conversion, preserve completed files, and see cancelled/skipped files in the report. Partial output files are removed; existing destinations are replaced only after a conversion succeeds.
- Limit each output to a requested size for JPG, WEBP or AVIF. Enable **Limit file size**, enter a limit in KiB (1 KiB = 1024 bytes), and select a supported output format. Quality is searched automatically up to the slider's **Max quality**; image dimensions and other settings stay as configured. Unreachable limits are reported without publishing an oversized file. Custom presets retain the limit.

- Compare an original with its processed output: select a successful row in the report and click **Compare images**. Move the before/after divider, choose **Fit to window**, or inspect at **100%** (one result pixel per physical display pixel). Resized originals are aligned to the result dimensions; animations show the first frame. Full-resolution comparison supports images up to 32 megapixels.

## Video compression

Switch to **Video**, add MP4, MOV or M4V files (or drop a folder), then choose **High quality**, **Balanced**, or **Smallest file**. Output is MP4 with H.264 video; select original resolution, up to 1080p, or up to 720p. Aspect ratio is preserved without enlarging smaller videos. Choose to keep audio, compress it, or remove it. Keep audio copies compatible tracks; incompatible audio is converted to high-quality AAC.

Select a queued video and click **Remove**, or press **Delete / Backspace** while its list has focus, to remove that video only. **Clear all** empties the queue. Original files stay on disk, and the queue cannot be changed during processing.

Choose an output file for one video or an output folder for a batch. Batch outputs receive unique filenames. Processing displays progress, supports cancellation, and preserves completed videos. The report includes sizes, savings, errors and elapsed time; **View last report** reopens it. Originals remain intact and partial files are removed. Results that would be larger than or equal to the input are reported without replacing the destination.

Enable **Limit file size** and enter a limit in decimal MB (1 MB = 1,000,000 bytes) to cap each video's output. PixelKit keeps the selected preset's result when it already fits; otherwise it measures the selected audio and uses two-pass H.264 encoding with automatic bitrate selection. It checks the actual MP4 size and retries at a lower bitrate when needed. This can take longer than ordinary compression. Resolution and audio choices are retained; if the limit cannot be reached, the report suggests a larger limit, a lower resolution, or compressed/removed audio. Oversized results are never published, and originals and existing destinations survive failure or cancellation. The report includes the requested limit and exact actual size.

Use **Save preset…** in the video Compression card to keep your quality, resolution, audio choice and optional file-size limit. Choose a saved preset to apply it to the entire video queue. **Rename…** changes its name; **Delete** removes it after confirmation. Replacing an existing name also requires confirmation. Video presets survive app restarts and are stored separately from image presets in your local preferences. Editing a video setting switches to **Custom settings**. Presets keep the current input files and destination, do not start processing, and cannot be changed while processing is active.

This version supports SDR videos. HDR, wide-gamut and transparent video sources receive a clear unsupported-video error. Output retains the main video and all selected audio tracks; subtitles, extra video tracks, chapters and metadata are omitted. Video playback/comparison and hardware acceleration are not included yet. All processing remains local.

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

Use Python 3.10 or newer, ImageMagick 7, and FFmpeg/FFprobe with H.264 (`libx264`) and AAC encoding. Install the project once in a virtual environment, then launch it as a package.

### macOS

```sh
brew install python@3.12 imagemagick ffmpeg
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pixelkit
```

### Windows

Install ImageMagick 7 and a FFmpeg build with `ffmpeg.exe` and `ffprobe.exe` available in PATH, then run:

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
  onboarding.py    First-run introduction and getting-started workflow
  onboarding_art.py Vector artwork for the welcome screen
  updates.py       Stable release versions and matching installers
  update_dialog.py Manual update checks and download links
  presets.py       Built-in presets and saved preferences
  runtime.py       Platform resources and ImageMagick discovery
  widgets.py       Shared dropdown widgets
  report.py        Per-file results and compression report
  bug_report.py    Sanitized bug diagnostics, GitHub links and safe report export
  bug_report_dialog.py Editable bug-report drafts and preview
  comparison.py    On-demand original/result comparison
  video.py         Video probing, compression and cancellable worker
  video_panel.py   Video settings, queue and result report
  video_presets.py Validated custom video presets and local preferences
  assets/          Logo, Windows icon, and SVG controls
packaging/         Platform-specific build configuration and installer
scripts/           macOS builder and bundle verification
tests/             Application, preset, and runtime checks
.github/workflows/ macOS build automation
pyproject.toml     Dependencies, package metadata, and launch command
```

Dependencies are declared in `pyproject.toml`; the version is declared in `src/pixelkit/__init__.py`. `build/` contains disposable build intermediates; `dist/` contains local deliverables. Both are ignored by Git. Close any app launched from `dist/` before removing or rebuilding that bundle.

To publish a macOS release, update the source version on `main`, then push a matching stable tag such as `v1.13.0`. The **macOS app** workflow builds and verifies both architectures before publishing their DMGs together in GitHub Releases. PR, branch and manual builds only upload workflow artifacts. A mismatched tag or missing installer prevents publication; existing published releases are not overwritten. Release packaging and signing requirements are described in the macOS instructions.

## Development and validation

See [macOS packaging](packaging/macos/README.md) and [Windows packaging](packaging/windows/README.md). Install with `pip install -e '.[build]'` to include the optional PyInstaller dependency for native builds.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover presets, file protection, batch image/video conversion, Finder events, and resource discovery on Windows and macOS. ImageMagick and FFmpeg are required for the media integration checks. Every macOS build also verifies embedded signatures, library paths, image codecs, packaged assets, and GUI startup.

## Security

See [SECURITY.md](SECURITY.md) for the vulnerability reporting policy.
