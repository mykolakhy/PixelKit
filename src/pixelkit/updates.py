"""Validate GitHub release metadata and choose a compatible installer."""
from __future__ import annotations

import re
from dataclasses import dataclass


LATEST_RELEASE_API = "https://api.github.com/repos/mykolakhy/PixelKit/releases/latest"
RELEASES_URL = "https://github.com/mykolakhy/PixelKit/releases"

_STABLE_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_ARCHITECTURES = {
    "arm64": "arm64",
    "aarch64": "arm64",
    "x86_64": "x86_64",
    "amd64": "x86_64",
}
_DOWNLOAD_LABELS = {
    "arm64": "Download for macOS (Apple silicon)",
    "x86_64": "Download for macOS (Intel)",
}


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    download_url: str | None
    download_label: str | None
    is_newer: bool


def _version(value: object, *, published: bool) -> tuple[str, tuple[int, int, int]]:
    label = "release tag" if published else "installed version"
    if not isinstance(value, str) or (published and not value.startswith("v")):
        raise ValueError(f"The {label} is not a stable version.")
    version = value[1:] if published else value
    match = _STABLE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"The {label} must use {'v' if published else ''}N.N.N without leading zeroes.")
    try:
        numbers = tuple(int(component) for component in match.groups())
    except ValueError as error:
        raise ValueError(f"The {label} is too large to read.") from error
    return version, numbers


def _canonical_url(value: object, expected: str, *, label: str) -> str:
    # GitHub supplies canonical URLs. Exact matching also excludes credentials,
    # extra ports, query strings, encoded path tricks, and another repository.
    if not isinstance(value, str) or value != expected:
        raise ValueError(f"The {label} is not a valid PixelKit GitHub release URL.")
    return value


def parse_release(payload: object, current_version: str, *, system: str, machine: str) -> ReleaseInfo:
    """Read one stable release; never invent an installer URL or architecture."""
    if not isinstance(payload, dict):
        raise ValueError("The update response is not a release object.")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("The update response is not a published stable release.")

    version, released_numbers = _version(payload.get("tag_name"), published=True)
    _, installed_numbers = _version(current_version, published=False)
    tag = f"v{version}"
    page_url = _canonical_url(payload.get("html_url"), f"{RELEASES_URL}/tag/{tag}", label="release page")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("The release assets are not a list.")
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str) or not asset["name"]:
            raise ValueError("The release contains an invalid asset.")

    download_url = None
    download_label = None
    arch = _ARCHITECTURES.get(machine.lower()) if isinstance(machine, str) else None
    if isinstance(system, str) and system.lower() == "darwin" and arch is not None:
        expected_name = f"PixelKit-{version}-macOS-{arch}.dmg"
        matching = [asset for asset in assets if asset["name"] == expected_name]
        if len(matching) > 1:
            raise ValueError("The release contains duplicate installers for this Mac.")
        if matching:
            asset = matching[0]
            size = asset.get("size")
            if not isinstance(asset.get("state"), str) or type(size) is not int or size < 0:
                raise ValueError("The release installer has invalid upload details.")
            if asset["state"] == "uploaded" and size > 0:
                download_url = _canonical_url(
                    asset.get("browser_download_url"),
                    f"{RELEASES_URL}/download/{tag}/{expected_name}",
                    label="installer download",
                )
                download_label = _DOWNLOAD_LABELS[arch]

    return ReleaseInfo(
        version=version,
        page_url=page_url,
        download_url=download_url,
        download_label=download_label,
        is_newer=released_numbers > installed_numbers,
    )
