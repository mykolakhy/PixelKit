"""Validated processing presets, stored in the user's native app preferences."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from PyQt6.QtCore import QSettings

OUTPUT_FORMATS = ["Automatic", "JPG", "PNG", "WEBP", "AVIF", "GIF", "BMP", "TIFF"]


def preset_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 60 or any(ord(char) < 32 for char in name):
        raise ValueError("Enter a preset name of 1–60 characters.")
    if name.casefold() in {name.casefold() for name in BUILTIN_PRESETS} | {"custom settings"}:
        raise ValueError("Choose a different name; this name belongs to a built-in preset.")
    return name


@dataclass(frozen=True)
class Preset:
    resize_mode: str = "dimensions"
    width: int | None = None
    height: int | None = None
    longest_side: int | None = None
    keep_ratio: bool = True
    quality: int = 82
    strip_metadata: bool = True
    background: str = "#ffffff"
    output_format: str = "Automatic"

    def __post_init__(self) -> None:
        if self.resize_mode not in ("dimensions", "longest_side"):
            raise ValueError("Choose a supported resize mode.")
        for value in (self.width, self.height, self.longest_side):
            if value is not None and (type(value) is not int or not 1 <= value <= 100000):
                raise ValueError("Image dimensions must be between 1 and 100000 pixels, or empty.")
        if type(self.quality) is not int or not 10 <= self.quality <= 100:
            raise ValueError("Quality must be between 10 and 100.")
        if type(self.keep_ratio) is not bool or type(self.strip_metadata) is not bool:
            raise ValueError("Invalid checkbox values in this preset.")
        if not isinstance(self.background, str) or not self.background.strip() or len(self.background) > 128 or any(ord(char) < 32 for char in self.background):
            raise ValueError("Enter a JPEG background color before saving a preset.")
        if self.output_format not in OUTPUT_FORMATS:
            raise ValueError("Choose a supported output format.")

    def description(self) -> str:
        if self.resize_mode == "longest_side" and self.longest_side:
            size = f"Longest side {self.longest_side} px"
        elif self.resize_mode == "dimensions" and (self.width or self.height):
            size = f"{self.width or 'Original'} × {self.height or 'Original'} px"
        else:
            size = "Original dimensions"
        return f"{self.output_format} • {size} • Quality {self.quality}"


BUILTIN_PRESETS = {
    "For website": Preset(resize_mode="longest_side", longest_side=1920, quality=80, output_format="WEBP"),
    "For email": Preset(resize_mode="longest_side", longest_side=1600, quality=75, output_format="JPG"),
    "PNG · original size": Preset(quality=100, output_format="PNG"),
}


class PresetStore:
    KEY = "presets/custom_v1"

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else QSettings("com.pixelkit", "PixelKit")
        self.settings.setFallbacksEnabled(False)
        self.load_error = False

    def load(self) -> dict[str, Preset]:
        raw = self.settings.value(self.KEY, "")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            if type(payload) is not dict or payload.get("version") != 1 or type(payload.get("presets")) is not dict:
                raise ValueError("Invalid preset file")
            presets = {}
            names = set()
            for name, values in payload["presets"].items():
                clean_name = preset_name(name)
                if clean_name.casefold() in names:
                    raise ValueError("Duplicate preset name")
                presets[clean_name] = Preset(**values)
                names.add(clean_name.casefold())
            return presets
        except (ValueError, TypeError, AttributeError):
            self.load_error = True
            return {}

    def save(self, presets: dict[str, Preset]) -> None:
        # A failed native-preferences write must not appear as a successful save.
        if not self.settings.isWritable():
            raise OSError("PixelKit cannot write its saved presets. Check your app preferences permissions.")
        if self.load_error:
            self.settings.setValue("presets/recovery_backup", self.settings.value(self.KEY, ""))
        payload = {"version": 1, "presets": {name: asdict(preset) for name, preset in presets.items()}}
        self.settings.setValue(self.KEY, json.dumps(payload, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("PixelKit could not save its presets. Please try again.")
        self.load_error = False
