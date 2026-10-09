"""Validate a stable macOS release and optionally publish its two disk images."""
from __future__ import annotations

import argparse
import ast
import re
import stat
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT / "src" / "pixelkit" / "__init__.py"
VERSION_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
ARCHITECTURES = ("arm64", "x86_64")


class ReleaseError(ValueError):
    """The tag, source version or artifacts are unsuitable for publication."""


def source_version(version_file: Path) -> str:
    """Read the literal source version without importing application dependencies."""
    try:
        tree = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))
    except (OSError, SyntaxError, UnicodeError) as error:
        raise ReleaseError(f"Cannot read source version from {version_file}: {error}") from error
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            values.append(node.value)
    if len(values) != 1 or not isinstance(values[0], ast.Constant) or not isinstance(values[0].value, str):
        raise ReleaseError("Source must define exactly one literal string __version__.")
    version = values[0].value
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ReleaseError(f"Source version {version!r} must use N.N.N without leading zeroes.")
    return version


def validate_tag(tag: str, version_file: Path = VERSION_FILE) -> str:
    match = re.fullmatch(rf"v({VERSION_PATTERN})", tag)
    if match is None:
        raise ReleaseError("Release tag must use vN.N.N with ASCII digits and no leading zeroes.")
    version = match.group(1)
    actual_version = source_version(version_file)
    if version != actual_version:
        raise ReleaseError(f"Tag {tag!r} does not match source version {actual_version!r}.")
    return version


def validate_assets(artifacts_dir: Path, version: str) -> tuple[Path, Path]:
    """Require only the two exact, nonempty, regular architecture disk images."""
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ReleaseError("Asset version must use N.N.N without leading zeroes.")
    try:
        if not stat.S_ISDIR(artifacts_dir.lstat().st_mode):
            raise ReleaseError("Artifact directory must be a real directory, not a symlink.")
        expected = tuple(artifacts_dir / f"PixelKit-{version}-macOS-{arch}.dmg" for arch in ARCHITECTURES)
        expected_names = {asset.name for asset in expected}
        actual_names = {entry.name for entry in artifacts_dir.iterdir()}
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            unexpected = sorted(actual_names - expected_names)
            raise ReleaseError(f"Release requires exactly both disk images; missing={missing}, unexpected={unexpected}.")
        for asset in expected:
            information = asset.lstat()
            if not stat.S_ISREG(information.st_mode) or information.st_size == 0:
                raise ReleaseError(f"Release asset must be a nonempty regular file, not a symlink: {asset.name}")
    except OSError as error:
        raise ReleaseError(f"Cannot inspect release artifacts: {error}") from error
    return expected


def publish_release(tag: str, artifacts_dir: Path, version_file: Path = VERSION_FILE) -> None:
    version = validate_tag(tag, version_file)
    assets = validate_assets(artifacts_dir, version)
    # Listing with write access includes drafts, so an interrupted previous run
    # also needs review rather than silently creating another release for its tag.
    existing = subprocess.run(
        ["gh", "api", "repos/{owner}/{repo}/releases", "--paginate", "--jq", ".[].tag_name"],
        check=True, capture_output=True, text=True,
    )
    if tag in existing.stdout.splitlines():
        raise ReleaseError(f"A release already exists for {tag}; its assets will not be changed.")
    # With assets, gh creates a draft, uploads both files and only then publishes.
    # Creation refuses an existing published release; never upload with --clobber.
    subprocess.run(
        ["gh", "release", "create", tag, *(str(asset.absolute()) for asset in assets),
         "--verify-tag", "--generate-notes", "--title", f"PixelKit {version}"],
        check=True,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Stable release tag, such as v1.13.0")
    parser.add_argument("--version-file", type=Path, default=VERSION_FILE)
    parser.add_argument("--artifacts-dir", type=Path, help="Directory containing both downloaded disk images")
    parser.add_argument("--publish", action="store_true", help="Create a public GitHub Release using GH_TOKEN and GH_REPO")
    args = parser.parse_args(argv)
    if args.publish and args.artifacts_dir is None:
        parser.error("--publish requires --artifacts-dir")
    try:
        if args.publish:
            publish_release(args.tag, args.artifacts_dir, args.version_file)
        else:
            version = validate_tag(args.tag, args.version_file)
            if args.artifacts_dir is not None:
                validate_assets(args.artifacts_dir, version)
            print(version)
    except (ReleaseError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release failed: {error}\n")


if __name__ == "__main__":
    main()
