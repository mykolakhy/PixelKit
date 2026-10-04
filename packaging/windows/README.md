# Packaging for Windows

Build on Windows with Python 3.10 or newer. The app and installer have separate entry points: `src/pixelkit/__main__.py` launches the Qt app; `packaging/windows/installer.py` installs a bundled `payload` and creates desktop and Start menu shortcuts.

Install dependencies from the repository root:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e '.[build]'
.venv\Scripts\python -m unittest discover -s tests -v
```

Install ImageMagick 7 and place a complete matching Windows runtime in `C:\ImageMagick`, or replace that example path below. It must include `magick.exe`, its libraries, codecs, and configuration. Run these commands from the repository root:

```powershell
.venv\Scripts\python -m PyInstaller --noconfirm --windowed --name PixelKit --paths src --icon src/pixelkit/assets/PixelKit.ico --add-data "src/pixelkit/assets:pixelkit/assets" --add-data "C:\ImageMagick:imagemagick" --exclude-module tkinter --distpath dist/windows --workpath build/windows/app --specpath build/windows src/pixelkit/__main__.py
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name PixelKit-Setup --icon src/pixelkit/assets/PixelKit.ico --add-data "dist/windows/PixelKit:payload" --distpath dist/windows --workpath build/windows/installer --specpath build/windows packaging/windows/installer.py
```

The first build produces `dist/windows/PixelKit/PixelKit.exe`. Verify this app on Windows before creating or distributing the installer. The second build produces `dist/windows/PixelKit-Setup.exe`, bundling the entire app folder as its payload.

The installer copies its payload to `%LOCALAPPDATA%/Programs/PixelKit`, creates shortcuts, and offers to launch the app. Source launchers use the repository's `.venv`, with no machine-specific Python paths.

## Local legacy installer

The original prebuilt installer was moved out of `outputs/` into ignored `dist/windows/PixelKit-Setup.exe` during repository cleanup. It has not been rebuilt with the current interface. Preserve or rename it before building a replacement if you still need that version. It also remains recoverable from Git history.

Native Windows packaging and installation cannot be verified on macOS. Before publishing, check the standalone app on a Windows machine without Python or ImageMagick, run real conversions, and test installation and shortcuts.
