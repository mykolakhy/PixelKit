"""Immutable drop batches and safe output publication for quick processing."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pixelkit.presets import Preset
    from pixelkit.video import VideoSettings

ENABLED_KEY = "quick_processing/enabled"
FOLDER_KEY = "quick_processing/folder"


@dataclass(frozen=True)
class QuickBatch:
    media: str
    sources: tuple[Path, ...]
    outputs: tuple[Path, ...]
    settings: Preset | VideoSettings
    commands: tuple[tuple[str, ...], ...] = ()


def unique_outputs(files: list[tuple[Path, str]], folder: Path | None, protected: list[Path]) -> tuple[Path, ...]:
    """Allocate new names, accounting for originals, symlinks and queued jobs."""
    used = {str(path.resolve()).casefold() for path in protected}
    outputs = []
    for source, extension in files:
        parent = folder if folder is not None else source.parent
        candidate = parent / f"{source.stem}_optimized.{extension}"
        counter = 2
        while candidate.exists() or candidate.is_symlink() or str(candidate.resolve()).casefold() in used:
            candidate = parent / f"{source.stem}_optimized_{counter}.{extension}"
            counter += 1
        used.add(str(candidate.resolve()).casefold())
        outputs.append(candidate)
    return tuple(outputs)


def publish_output(temporary: Path, output: Path, *, overwrite: bool) -> None:
    if overwrite:
        temporary.replace(output)
    else:
        # Both paths are in the output directory's filesystem. An exclusive
        # hard link publishes the complete file without replacing a file that
        # appeared after allocation. Cleanup removes the temporary link.
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            raise FileExistsError("The output filename is now in use. Drop the original again to save a new copy; no existing file was replaced.") from exc
