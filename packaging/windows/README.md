# Packaging for Windows

Build on Windows with Python 3.10 or newer. The app and installer have separate entry points: `src/pixelkit/__main__.py` launches the Qt app; `packaging/windows/installer.py` installs a bundled `payload` and creates desktop and Start menu shortcuts.

Install dependencies from the repository root:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e '.[build]'
.venv\Scripts\python -m unittest discover -s tests -v
```

Install ImageMagick 7 and place a complete matching Windows runtime in `C:\ImageMagick`, or replace that example path below. It must include `magick.exe`, its libraries, codecs, and configuration. Place a complete Windows FFmpeg distribution in `C:\FFmpeg` with `bin\ffmpeg.exe`, `bin\ffprobe.exe` and its license files. A static build with `libx264` and `aac` encoders avoids missing DLLs; if using a shared build, retain all its DLLs beside the executables. Run these commands from the repository root:

```powershell
.venv\Scripts\python -m PyInstaller --noconfirm --windowed --name PixelKit --paths src --icon src/pixelkit/assets/PixelKit.ico --add-data "src/pixelkit/assets:pixelkit/assets" --add-data "C:\ImageMagick:imagemagick" --add-data "C:\FFmpeg:ffmpeg" --exclude-module tkinter --distpath dist/windows --workpath build/windows/app --specpath build/windows src/pixelkit/__main__.py
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name PixelKit-Setup --icon src/pixelkit/assets/PixelKit.ico --add-data "dist/windows/PixelKit:payload" --distpath dist/windows --workpath build/windows/installer --specpath build/windows packaging/windows/installer.py
```

The first build produces `dist/windows/PixelKit/PixelKit.exe`. Verify this app on Windows before creating or distributing the installer. The second build produces `dist/windows/PixelKit-Setup.exe`, bundling the entire app folder as its payload.

The installer checks that the app, FFmpeg and FFprobe are present before copying its payload to `%LOCALAPPDATA%/Programs/PixelKit`, creating shortcuts, and offering to launch the app. The application version comes from `src/pixelkit/__init__.py` (currently 1.6.0).

Native Windows packaging and installation cannot be verified on macOS. Before publishing, check the standalone app on a Windows machine without Python, ImageMagick or FFmpeg; run image conversions and MP4/MOV/M4V compression, test every audio mode and cancellation, and test installation and shortcuts.

FFmpeg builds containing `libx264` use GPL licensing. Retain the distribution's license files and provide the corresponding source and build materials for the binaries you distribute. See [FFmpeg's license and legal guidance](https://ffmpeg.org/legal.html).
