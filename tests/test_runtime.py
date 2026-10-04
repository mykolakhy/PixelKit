from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pixelkit.runtime as runtime


class RuntimeTests(unittest.TestCase):
    def test_installed_package_contains_all_ui_assets(self):
        for name in ("PixelKit.png", "PixelKit.ico", "check.svg", "chevron-down.svg", "chevron-up.svg"):
            with self.subTest(name=name):
                self.assertTrue(runtime.resource_path(name).is_file())

    def test_frozen_package_assets_take_priority_over_legacy_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "pixelkit" / "assets"
            assets.mkdir(parents=True)
            (assets / "PixelKit.png").touch()
            (root / "PixelKit.png").touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(root), create=True):
                self.assertEqual(runtime.resource_path("PixelKit.png"), assets / "PixelKit.png")

    def test_frozen_resources_and_existing_windows_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            internal = root / "_internal"
            internal.mkdir()
            (root / "PixelKit.png").touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(internal), create=True), patch.object(sys, "executable", str(root / "PixelKit.exe")):
                self.assertEqual(runtime.resource_path("PixelKit.png"), root.resolve() / "PixelKit.png")
                (internal / "PixelKit.png").touch()
                self.assertEqual(runtime.resource_path("PixelKit.png"), internal / "PixelKit.png")

    def test_macos_bundle_takes_priority_over_system_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            magick = root / "imagemagick" / "bin" / "magick"
            magick.parent.mkdir(parents=True)
            magick.touch(mode=0o755)
            with patch.object(sys, "platform", "darwin"), patch.object(runtime, "resource_roots", return_value=[root]), patch.object(runtime.shutil, "which", return_value="/usr/local/bin/magick"):
                self.assertEqual(runtime.find_magick(), str(magick))

    def test_finder_searches_both_homebrew_prefixes_and_macports(self):
        with patch.object(sys, "platform", "darwin"), patch.object(runtime, "resource_roots", return_value=[]), patch.object(runtime.shutil, "which", return_value=None), patch.object(runtime.os, "access", return_value=True):
            for installed in ("/opt/homebrew/bin/magick", "/usr/local/bin/magick", "/opt/local/bin/magick"):
                with self.subTest(installed=installed), patch.object(Path, "is_file", lambda path: str(path) == installed):
                    self.assertEqual(runtime.find_magick(), installed)

    def test_windows_bundled_executable_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            magick = root / "imagemagick" / "magick.exe"
            magick.parent.mkdir()
            magick.touch()
            with patch.object(sys, "platform", "win32"), patch.object(runtime, "resource_roots", return_value=[root]), patch.object(runtime.shutil, "which", return_value=None):
                self.assertEqual(runtime.find_magick(), str(magick))

    def test_bundle_sets_configuration_and_dynamic_coder_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "imagemagick"
            config = root / "etc" / "ImageMagick-7"
            coders = root / "lib" / "ImageMagick" / "modules-Q16HDRI" / "coders"
            filters = coders.parent / "filters"
            for path in (config, coders, filters):
                path.mkdir(parents=True)
            original = {"PATH": "/usr/bin:/bin", "MAGICK_CODER_MODULE_PATH": "/old/path"}
            with patch.object(sys, "platform", "darwin"):
                env = runtime.magick_environment(str(root / "bin" / "magick"), original)
                self.assertEqual(env["MAGICK_CONFIGURE_PATH"], str(config))
                self.assertEqual(env["MAGICK_CODER_MODULE_PATH"], str(coders))
                self.assertEqual(env["MAGICK_FILTER_MODULE_PATH"], str(filters))
                self.assertEqual(env["PATH"], original["PATH"])
                self.assertEqual(original["MAGICK_CODER_MODULE_PATH"], "/old/path")
                self.assertEqual(runtime.magick_environment("/opt/homebrew/bin/magick", original), original)

    def test_nonexecutable_posix_runtime_is_rejected(self):
        with patch.object(sys, "platform", "darwin"), patch.object(runtime, "resource_roots", return_value=[]), patch.object(runtime.shutil, "which", return_value="/fake/magick"), patch.object(Path, "is_file", return_value=True), patch.object(runtime.os, "access", return_value=False):
            self.assertIsNone(runtime.find_magick())

    def test_only_windows_processes_use_no_console_flag(self):
        with patch.object(runtime.subprocess, "run") as run, patch.object(runtime.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
            for platform in ("win32", "darwin"):
                with self.subTest(platform=platform), patch.object(sys, "platform", platform):
                    runtime.run_magick(["magick", "-version"], env={"PATH": "/bin"})
                    kwargs = run.call_args.kwargs
                    self.assertEqual("creationflags" in kwargs, platform == "win32")
                    self.assertEqual(kwargs["env"], {"PATH": "/bin"})


if __name__ == "__main__":
    unittest.main()
