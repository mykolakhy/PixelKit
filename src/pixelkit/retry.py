"""Read-only destination checks shared by image and video retry preparation."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pixelkit.report import BatchReport


def validate_retry_output(report: BatchReport, output: Path, *, single: bool) -> None:
    """Recheck current paths before restoring a queue, without creating files."""
    if single:
        if output.is_dir():
            raise ValueError("Choose an output file, rather than an existing folder.")
        protected = [file.source for file in report.files] + [file.output for file in report.successful]
        if any(
            str(output.resolve()).casefold() == str(path.resolve()).casefold()
            or (output.exists() and path.exists() and output.samefile(path))
            for path in protected
        ):
            raise ValueError("The retry output would replace an original file or an earlier successful result. Choose another output location.")
        directory = output.parent
    else:
        directory = output
    if directory.exists() and not directory.is_dir():
        raise ValueError("The retry output folder is occupied by a file. Choose another output location.")
    ancestor = directory
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError("The retry output folder cannot be prepared at this location.")
