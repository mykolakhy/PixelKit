# Building PixelKit for macOS

Build on the target architecture: Apple Silicon produces `arm64`; an Intel Mac produces `x86_64`. The build packages Python, PyQt6/Qt, ImageMagick 7, FFmpeg, FFprobe, their required libraries, image codec configuration, and dependency license notices. No Homebrew installation is needed on the user's Mac.

## Build

Install [Homebrew](https://brew.sh/) if needed, then run from the repository root:

```sh
brew install python@3.12 imagemagick ffmpeg
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[build]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/build_macos.py
```

The builder reads the version from `src/pixelkit/__init__.py`. Pass `--version 1.2.3` to override it for a release. The builder creates:

```text
dist/macos/arm64/PixelKit.app
dist/macos/arm64/PixelKit-1.6.0-macOS-arm64.dmg
```

On Intel, the directory and disk image name use `x86_64`. Open the `.dmg` and drag **PixelKit.app** onto the **Applications** shortcut. The app supports Finder **Open With**, dropping images or MP4/MOV/M4V videos on its Dock icon, and ⌘O, ⌘S, ⌘W, and ⌘Q.

Use `--no-dmg` to create only the app. Use `--magick-prefix /path/to/imagemagick` for a custom ImageMagick installation containing `bin/magick`, `lib`, and `etc`; use `--ffmpeg-prefix /path/to/ffmpeg` for a custom installation containing `bin/ffmpeg` and `bin/ffprobe`. FFmpeg must include the `libx264` video and `aac` audio encoders. Python and both media runtimes must contain the same architecture. The normal build uses the locally installed Homebrew runtimes.

Close the app before rebuilding the same bundle. If it must stay open, pass `--output-dir` with a different directory. The architecture subdirectory is added automatically. Normal builds keep only the `.app` and `.dmg`; the duplicate PyInstaller collection and disk-image staging folder are removed automatically.

The app declares the build host's macOS major version as its minimum. Build on the oldest macOS version you intend to support, using compatible Python, Qt, ImageMagick and FFmpeg dependencies; building on a newer OS does not establish compatibility with older ones. The GitHub workflow uses macOS 15 for both architectures. A local build on macOS 26 targets macOS 26 or later.

## Checks and continuous integration

`build_macos.py` verifies embedded code signatures and runs `scripts/verify_macos.py` before creating the disk image. Verification rejects libraries pointing outside the app except Apple system libraries; checks JPG, PNG, WEBP, AVIF, GIF, BMP, TIFF, HEIC, and ICO encoding/decoding with a minimal environment and isolated home directory; encodes, probes and decodes a short H.264/AAC video using only the bundled FFmpeg tools; verifies video document registration; and starts the packaged GUI without a developer shell.

You can repeat the package checks with:

```sh
.venv/bin/python scripts/verify_macos.py dist/macos/arm64/PixelKit.app
```

The **macOS app** GitHub Actions workflow runs on pull requests, pushes to `main`, version tags, and manual dispatch. It uploads separate `.dmg` artifacts for Apple Silicon and Intel. Version tags must use the form `v1.2.3`; other runs use `pixelkit.__version__` from `src/pixelkit/__init__.py`. Download the artifact from the completed workflow run and extract its ZIP to get the disk image. Build outputs are ignored by Git.

## Signing for distribution

Local builds are ad hoc signed by PyInstaller. This allows local execution but does not identify an Apple-approved developer or provide notarization. Downloaded builds may require **System Settings → Privacy & Security → Open Anyway** after the first launch attempt.

To sign with an installed Developer ID certificate:

```sh
export PIXELKIT_CODESIGN_IDENTITY='Developer ID Application: Your Name (TEAMID)'
.venv/bin/python scripts/build_macos.py
```

PyInstaller signs the embedded binaries and the app with that identity and the hardened runtime. For public distribution, submit the resulting disk image to Apple's notary service using a Keychain profile you have configured, then staple the ticket:

```sh
xcrun notarytool submit dist/macos/arm64/PixelKit-1.6.0-macOS-arm64.dmg \
  --keychain-profile PixelKit --wait
xcrun stapler staple dist/macos/arm64/PixelKit-1.6.0-macOS-arm64.dmg
```

Check the notary result before publishing. The repository workflow produces ad hoc builds; it does not contain signing credentials, notarize artifacts, or publish releases.

FFmpeg builds with `libx264` use GPL licensing. The bundle retains the installed license files and FFmpeg version/configuration in `Contents/Resources/licenses`. Distribution also requires the corresponding source and build materials for those binaries; keeping license text alone is insufficient. See [FFmpeg's license and legal guidance](https://ffmpeg.org/legal.html).

Packaging behavior follows the [PyInstaller macOS documentation](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing) and [ImageMagick runtime configuration](https://imagemagick.org/resources/).
