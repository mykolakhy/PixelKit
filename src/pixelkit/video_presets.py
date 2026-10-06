"""Validated video presets in the user's native app preferences."""
from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict

from PyQt6.QtCore import QSettings

from pixelkit.video import VideoSettings


def video_preset_name(value: str) -> str:
    if type(value) is not str:
        raise ValueError("Enter a preset name of 1–60 characters.")
    name = value.strip()
    if not name or len(name) > 60 or any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("Enter a preset name of 1–60 characters without control characters.")
    if name.casefold() == "custom settings":
        raise ValueError("Choose a different name; this name belongs to custom settings.")
    return name


def _validate_settings(settings: VideoSettings) -> VideoSettings:
    if type(settings) is not VideoSettings:
        raise ValueError("Choose valid video settings before saving a preset.")
    # Revalidate even instances made without the normal dataclass constructor.
    values = asdict(settings)
    if type(values["preset"]) is not str or type(values["audio"]) is not str:
        raise ValueError("Choose valid video quality and audio settings.")
    validated = VideoSettings(**values)
    target = validated.target_bytes
    if target is not None and (not 1000 <= target <= 1_000_000_000_000 or target % 1000):
        raise ValueError("File-size limit must be between 0.001 and 1000000 MB, with at most three decimal places.")
    return validated


def video_preset_description(settings: VideoSettings) -> str:
    settings = _validate_settings(settings)
    quality = {"high": "High quality", "balanced": "Balanced", "small": "Smallest file"}[settings.preset]
    resolution = f"{settings.max_height}p" if settings.max_height else "Original resolution"
    audio = {"keep": "Keep audio", "compress": "Compress audio", "remove": "No audio"}[settings.audio]
    target = ""
    if settings.target_bytes is not None:
        # Integer arithmetic preserves exactly the decimals represented by the UI.
        megabytes, remainder = divmod(settings.target_bytes, 1_000_000)
        decimals = f"{remainder // 1000:03d}".rstrip("0")
        limit = f"{megabytes}.{decimals}" if decimals else str(megabytes)
        target = f" • ≤ {limit} MB"
    return f"{quality} • {resolution} • {audio}{target}"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key in video presets.")
        result[key] = value
    return result


def _validated_presets(presets: dict[str, VideoSettings]) -> dict[str, VideoSettings]:
    if type(presets) is not dict:
        raise ValueError("Invalid video presets.")
    validated = {}
    names = set()
    for name, settings in presets.items():
        clean_name = video_preset_name(name)
        folded = clean_name.casefold()
        if folded in names:
            raise ValueError("Each video preset must have a different name.")
        validated[clean_name] = _validate_settings(settings)
        names.add(folded)
    return validated


class VideoPresetStore:
    KEY = "video_presets/custom_v1"
    BACKUP_KEY = "video_presets/recovery_backup"

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else QSettings("com.pixelkit", "PixelKit")
        self.settings.setFallbacksEnabled(False)
        self.load_error = False

    def load(self) -> dict[str, VideoSettings]:
        raw = self.settings.value(self.KEY, "")
        if raw == "" or raw is None:
            self.load_error = False
            return {}
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_json_object)
            if (type(payload) is not dict or set(payload) != {"version", "presets"}
                    or type(payload["version"]) is not int or payload["version"] != 1
                    or type(payload["presets"]) is not dict):
                raise ValueError("Invalid video preset file.")
            presets = {}
            for name, values in payload["presets"].items():
                if type(values) is not dict:
                    raise ValueError("Invalid video settings.")
                presets[name] = VideoSettings(**values)
            result = _validated_presets(presets)
        except (ValueError, TypeError, AttributeError):
            self.load_error = True
            return {}
        self.load_error = False
        return result

    def save(self, presets: dict[str, VideoSettings]) -> None:
        validated = _validated_presets(presets)
        payload = {"version": 1, "presets": {name: asdict(settings) for name, settings in validated.items()}}
        encoded = json.dumps(payload, ensure_ascii=False)
        if not self.settings.isWritable():
            raise OSError("PixelKit cannot write its saved video presets. Check your app preferences permissions.")
        previous = {
            key: (self.settings.contains(key), self.settings.value(key))
            for key in (self.KEY, self.BACKUP_KEY)
        }
        if self.load_error:
            self.settings.setValue(self.BACKUP_KEY, self.settings.value(self.KEY, ""))
        self.settings.setValue(self.KEY, encoded)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            # QSettings also caches a failed write. Restore that cache so reopening
            # settings in this process cannot mistake the failed save for success.
            for key, (existed, value) in previous.items():
                if existed:
                    self.settings.setValue(key, value)
                else:
                    self.settings.remove(key)
            self.settings.sync()
            # A native write error remains in this QSettings instance, even if
            # preferences become writable again; restarting creates a fresh one.
            raise OSError("PixelKit could not save its video presets. Restart PixelKit and try again.")
        self.load_error = False
