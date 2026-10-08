"""Local, editable bug reports with private file details removed by default."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote, urlencode

from PyQt6.QtCore import QIODevice, QOperatingSystemVersion, QSaveFile, QSysInfo

from pixelkit import __version__

if TYPE_CHECKING:
    from pixelkit.report import FileResult


ISSUE_URL = "https://github.com/mykolakhy/PixelKit/issues/new"
URL_LIMIT = 7000


@dataclass(frozen=True)
class BugReportContext:
    mode: str = "Images"
    file: FileResult | None = None
    protected_paths: tuple[Path, ...] = ()


# A slash inside a URL or a converter source-code location is not a local path.
_PATH_START = r"(?<![\w/\\])(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/(?!/))"
_FILE_URI_START = r"\bfile:(?://|/(?!/)|[A-Za-z]:[\\/])"
_FILE_URI = re.compile(_FILE_URI_START + r"[^\r\n\"'`<>|;]*", re.IGNORECASE)
_FILE_URI_WITH_SUFFIX = re.compile(
    _FILE_URI_START + r"[^\r\n\"'`<>|;]*?\.[\w-]{1,16}(?=$|[\s\"'`:,);.])", re.IGNORECASE
)
# Escaped delimiters belong to a filename, as in Python's OSError repr. The
# other kind of quote can also occur inside the name without ending it.
_QUOTED_VALUE = r"(?:\\[^\r\n]|(?!(?P=quote))[^\r\n\\]|(?=(?P=quote))(?<=\w)'(?=\w))*?"
_QUOTED_PATH_WITH_SUFFIX = re.compile(
    r"(?P<quote>[\"'`])(?P<path>(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/(?!/))" + _QUOTED_VALUE + r"\.[\w-]{1,16})(?P=quote)"
)
_QUOTED_PATH = re.compile(
    r"(?P<quote>[\"'`])(?P<path>(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/(?!/))" + _QUOTED_VALUE + r")(?P=quote)"
)
_PATH_WITH_SUFFIX = re.compile(
    _PATH_START + r"[^\r\n\"'`<>|;]*?\.[\w-]{1,16}(?=$|[\s\"'`:,);.])"
)
_UNQUOTED_PATH = re.compile(
    r"(?<![\w/\\\"'`])(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/(?!/))[^\r\n\"`<>|;]*"
)
_PATH_REASON = re.compile(
    r":\s|\s+-\s+|\s+@\s+(?:error|warning)\b|\s+(?=(?:because|for\s+(?:reading|writing|encoding|decoding|processing)|Permission denied|Access is denied|No such file or directory|Operation not permitted|Invalid data|could not|cannot|does not exist)\b)",
    re.IGNORECASE,
)
_MEDIA_SUFFIX = r"(?:avif|bmp|gif|heic|heif|ico|jpe?g|jxl|png|svg|tiff?|webp|3gp|avi|flv|m4v|mkv|mov|mp4|mpeg|mpg|mts|m2ts|ogv|vob|webm|wmv|tmp)"
_MEDIA_NAME = re.compile(
    r"(?<![\w/\\])[^\s\"'`<>|:;,/\\]+\." + _MEDIA_SUFFIX + r"(?![\w.])",
    re.IGNORECASE,
)
_QUOTED_MEDIA = re.compile(r"(?P<quote>[\"'`])(?P<name>" + _QUOTED_VALUE + r"\." + _MEDIA_SUFFIX + r")(?P=quote)", re.IGNORECASE)
_NAMED_MEDIA = re.compile(r"\b(?:file|image|input|output|from)\s+(?P<name>[^\r\n\"'`<>|:;/\\]*?\." + _MEDIA_SUFFIX + r")(?=$|[\s:,);])", re.IGNORECASE)
_HOME_USER = re.compile(r"/(?:Users|home|Documents and Settings)/([^/:\r\n\"'`]+)", re.IGNORECASE)


def _path_aliases(path: Path | str, *, include_name: bool = True) -> set[str]:
    """Generate spelling variants without resolving or reading the file."""
    value = str(path)
    aliases = {value, value.replace("\\", "/"), value.replace("/", "\\")}
    # PureWindowsPath also understands forward slashes on a non-Windows host.
    if include_name:
        aliases.add(PureWindowsPath(value).name)
        aliases.add(Path(value).name)
    aliases.update(alias.replace("\\", "\\\\") for alias in tuple(aliases))
    # Exceptions and converter stderr can quote the same path differently.
    # Include repr's chosen delimiter and either explicitly escaped delimiter,
    # after adding basenames so filename-only messages receive the same cover.
    for alias in tuple(aliases):
        aliases.update((repr(alias)[1:-1], alias.replace("'", "\\'"), alias.replace('"', '\\"'), alias.replace("'", "\\'").replace('"', '\\"')))
    aliases.update(quote(alias, safe="/:\\") for alias in tuple(aliases))
    return {alias for alias in aliases if alias and alias not in {"/", "\\", ".", "~"}}


def _unquoted_path_value(value: str, suffix_pattern: re.Pattern[str] = _PATH_WITH_SUFFIX) -> str:
    """Separate a private path from the converter explanation that follows it."""
    reason = _PATH_REASON.search(value)
    path = value[:reason.start()] if reason else value
    file_path = suffix_pattern.match(path)
    # A clear file extension ends a path before ordinary diagnostic prose.
    # A slash after it belongs to another directory component (which may itself
    # contain a dot and spaces), so keep that directory path together instead.
    if file_path is not None and not re.search(r"[/\\]", path[file_path.end():]):
        path = path[:file_path.end()]
    return path.rstrip()


def _sanitize(text: str, paths: tuple[Path, ...]) -> str:
    """Remove known names and unknown absolute paths, keeping full error text."""
    aliases: set[str] = set()
    usernames = {value for key in ("USER", "USERNAME", "LOGNAME") if (value := os.environ.get(key))}
    for key in ("HOME", "USERPROFILE"):
        if value := os.environ.get(key):
            aliases.update(_path_aliases(value, include_name=False))
    for path in paths:
        aliases.update(_path_aliases(path))
        for parent in (Path(path).parent, PureWindowsPath(str(path)).parent):
            if str(parent) not in {".", "/", "\\"} and parent.name:
                aliases.update(_path_aliases(str(parent), include_name=False))
        usernames.update(_HOME_USER.findall(str(path).replace("\\", "/")))
    # Discover names before replacing paths, so repeated basename-only messages
    # cannot reveal a name that was hidden elsewhere in the same error.
    for match in _FILE_URI.finditer(text):
        value = _unquoted_path_value(match.group(), _FILE_URI_WITH_SUFFIX)
        aliases.add(value)
        decoded = unquote(value)
        aliases.update(_path_aliases(decoded.split(":", 1)[1].lstrip("/")))
        usernames.update(_HOME_USER.findall(decoded.replace("\\", "/")))
    for pattern in (_QUOTED_PATH_WITH_SUFFIX, _QUOTED_PATH, _PATH_WITH_SUFFIX, _UNQUOTED_PATH):
        for match in pattern.finditer(text):
            value = match.groupdict().get("path") or match.group()
            if pattern is _UNQUOTED_PATH:
                value = _unquoted_path_value(value)
            values = {value}
            if match.groupdict().get("quote"):
                values.add(re.sub(r"\\([\\\"'`])", r"\1", value))
            for value in values:
                basename = PureWindowsPath(value).name
                aliases.update(_path_aliases(value, include_name=bool(re.search(r"\.[\w-]{1,16}$", basename)) or basename.startswith(".pixelkit-")))
                usernames.update(_HOME_USER.findall(unquote(value).replace("\\", "/")))
    for pattern in (_QUOTED_MEDIA, _NAMED_MEDIA):
        for match in pattern.finditer(text):
            name = match.group("name")
            aliases.add(name)
            if match.groupdict().get("quote"):
                aliases.update(_path_aliases(re.sub(r"\\([\\\"'`])", r"\1", name)))
    expressions = [re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)]
    expressions += [r"(?<!\w)" + re.escape(username) + r"(?!\w)" for username in usernames if username]
    if expressions:
        # One substitution avoids rewriting the placeholders for a user named
        # "file", or for media whose basename occurs inside another alias.
        text = re.sub("|".join(expressions), "[file]", text, flags=re.IGNORECASE)
    for pattern in (_QUOTED_PATH_WITH_SUFFIX, _QUOTED_PATH):
        text = pattern.sub(lambda match: match.group("quote") + "[path]" + match.group("quote"), text)
    text = _PATH_WITH_SUFFIX.sub("[path]", text)
    def redact_unquoted(match: re.Match[str]) -> str:
        value = match.group()
        path = _unquoted_path_value(value)
        return "[path]" + value[len(path):]
    text = _UNQUOTED_PATH.sub(redact_unquoted, text)
    return _MEDIA_NAME.sub("[file]", text)


def _os_description() -> str:
    """Use native OS version data, without a hostname or converter invocation."""
    current = QOperatingSystemVersion.current()
    parts = (current.majorVersion(), current.minorVersion(), current.microVersion())
    version = ".".join(str(part) for part in parts if part >= 0)
    if current.name() and version:
        return f"{current.name()} {version}"
    return f"{QSysInfo.kernelType()} {QSysInfo.kernelVersion()}".strip()


def diagnostics(context: BugReportContext) -> str:
    """Build automatic diagnostics from already captured processing results."""
    lines = [
        f"PixelKit version: {__version__}",
        f"OS: {_os_description()}",
        f"Architecture: {QSysInfo.currentCpuArchitecture()}",
        f"Mode: {context.mode}",
    ]
    file = context.file
    paths = context.protected_paths
    if file is not None:
        paths += (file.source, file.output)
    # Stopped work is not a converter failure. A general report has no file data.
    if file is not None and file.error and file.stopped is None and not file.succeeded:
        lines.extend((
            "Result: Failed",
            f"Source format: {file.source.suffix.lstrip('.').upper() or 'Unknown'}",
            f"Output format: {file.output.suffix.lstrip('.').upper() or 'Unknown'}",
        ))
        if file.before is not None:
            lines.append(f"Input size: {file.before} bytes")
        if file.elapsed_seconds is not None:
            lines.append(f"Processing time: {file.elapsed_seconds:g} seconds")
        if file.target_bytes is not None:
            lines.append(f"File-size limit: {file.target_bytes} bytes")
        if file.quality is not None:
            lines.append(f"Quality used: {file.quality}")
        settings = getattr(file, "processing_settings", ())
        if settings:
            lines.append("Processing settings:")
            lines.extend(f"  {label}: {value}" for label, value in settings)
        lines.extend(("", "Error:", file.error))
    return _sanitize("\n".join(lines), paths)


def compose_report(title: str, steps: str, actual: str, expected: str, technical: str) -> str:
    """Compose Markdown from the user's edited fields without changing them."""
    return (
        f"# {title}\n\n"
        f"## Steps to reproduce\n{steps}\n\n"
        f"## What happened\n{actual}\n\n"
        f"## Expected result\n{expected}\n\n"
        f"## Technical details\n{technical}\n"
    )


def github_issue_url(title: str, body: str) -> str | None:
    """Open a prefilled draft only when the complete encoded URL fits."""
    url = ISSUE_URL + "?" + urlencode({"title": title, "body": body})
    return url if len(url.encode("utf-8")) <= URL_LIMIT else None


def _check_destination(path: Path, protected_paths: tuple[Path, ...]) -> None:
    resolved = path.resolve()
    for protected in protected_paths:
        if str(resolved).casefold() == str(protected.resolve()).casefold() or (
            path.exists() and protected.exists() and path.samefile(protected)
        ):
            raise ValueError("Choose a different file: a bug report cannot replace an input or output file.")


def save_report(path: Path, text: str, protected_paths: tuple[Path, ...] = ()) -> None:
    """Save UTF-8 atomically, refusing every protected media file and alias."""
    _check_destination(path, protected_paths)
    destination = QSaveFile(str(path))
    destination.setDirectWriteFallback(False)
    try:
        data = text.encode("utf-8")
        if not destination.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(f"Could not open the report file: {destination.errorString()}")
        if destination.write(data) != len(data):
            raise OSError(f"Could not write the complete report: {destination.errorString()}")
        # Check again before commit in case a destination alias changed during
        # writing. QSaveFile keeps the previous contents until commit succeeds.
        _check_destination(path, protected_paths)
        if not destination.commit():
            raise OSError(f"Could not finish saving the report: {destination.errorString()}")
    except Exception:
        destination.cancelWriting()
        raise
