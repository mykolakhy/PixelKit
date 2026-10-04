# Development

Install PixelKit in editable mode before running scripts or tests. This makes every entry point use the same `src/pixelkit` package; imports do not depend on the working directory.

```sh
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -e '.[build]'
# Windows PowerShell
.venv\Scripts\python -m pip install -e '.[build]'
```

ImageMagick 7 must be installed for processing images from source. The optional `build` extra installs PyInstaller; regular source development only needs `pip install -e .`.

## Run and check

```sh
.venv/bin/python -m pixelkit
.venv/bin/python -m unittest discover -s tests -v
```

On Windows, substitute `.venv\Scripts\python`. Tests use an offscreen Qt interface and isolated preference files. The image conversion integration check is skipped if ImageMagick is unavailable; install it to run the complete suite.

## Ownership of files

- Application behavior belongs in `src/pixelkit/`. Reusable controls go in `widgets.py`; runtime discovery remains independent of Qt.
- Icons live in `src/pixelkit/assets/`. Package metadata includes these files in wheels; native packaging preserves the same resource layout.
- Platform build configuration and installer source belong in `packaging/`. Helper scripts belong in `scripts/`.
- Dependencies are declared only in `pyproject.toml`. The version is declared only in `src/pixelkit/__init__.py`.
- Keep permanent documentation in `docs/`; temporary screenshots, fixtures, and logs belong in `build/`.
- Store `.app`, `.dmg`, and `.exe` deliverables in ignored `dist/`, and distribute them as release or workflow artifacts.

Saved presets use the existing native user preferences. Moving source files does not change the preference keys or application identity.

## Cleaning local outputs

Close any app launched from `dist/` before removing or rebuilding that bundle. `build/` is disposable; `dist/` can be removed when its installers are no longer needed. Keep `.venv/` while developing. None of these folders is part of the source tree tracked by Git.

The visual audit is an archived report of the October 3, 2026 interface; its screenshots document that historical state, before the later preset and dropdown changes.
