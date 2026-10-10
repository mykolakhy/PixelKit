from __future__ import annotations

import plistlib
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import build_dmg as installer


class InstallerBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="PixelKit installer ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root / "PixelKit.app"
        resources = self.app / "Contents" / "Resources"
        resources.mkdir(parents=True)
        (resources / "PixelKit.icns").write_bytes(b"icon")
        (self.app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIconFile": "PixelKit"}))
        self.output = self.root / "installer.dmg"
        self.output.write_bytes(b"previous installer")
        self.copy = self.root / "mounted" / "PixelKit.app"

    def fake_build(self, filename, volume, *, settings, callback):
        Path(filename).write_bytes(b"new installer")
        callback({"type": "operation::finished", "operation": "file::add", "file": str(self.copy)})
        callback({"type": "operation::finished", "operation": "extensions::hide"})

    def build(self, builder=None, run=None, output=None):
        with patch.object(installer.sys, "platform", "darwin"), \
             patch.object(installer, "render_background", return_value=self.root / "background.png"), \
             patch.object(installer.subprocess, "run", run or Mock()), \
             patch.dict("sys.modules", {"dmgbuild": types.SimpleNamespace(build_dmg=builder or self.fake_build)}):
            return installer.build_dmg(self.app, output or self.output)

    def test_success_verifies_source_copy_and_image_before_replacing_installer(self):
        run = Mock()
        self.assertEqual(self.build(run=run), self.output)
        self.assertEqual(self.output.read_bytes(), b"new installer")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ["codesign", "--verify", "--deep", "--strict", str(self.app)])
        self.assertEqual(commands[1], ["codesign", "--verify", "--deep", "--strict", str(self.copy)])
        self.assertEqual(commands[2][:2], ["hdiutil", "verify"])
        self.assertEqual(list(self.root.glob(".pixelkit-dmg-*")), [])

    def test_failed_build_preserves_existing_installer_and_cleans_temporary_files(self):
        def fail(filename, *args, **kwargs):
            Path(filename).write_bytes(b"incomplete installer")
            raise RuntimeError("disk image creation failed")

        with self.assertRaisesRegex(RuntimeError, "creation failed"):
            self.build(builder=fail)
        self.assertEqual(self.output.read_bytes(), b"previous installer")
        self.assertEqual(list(self.root.glob(".pixelkit-dmg-*")), [])

    def test_failed_source_copy_or_image_verification_preserves_existing_installer(self):
        for failure in range(3):
            with self.subTest(failure=failure):
                run = Mock(side_effect=[None] * failure + [subprocess.CalledProcessError(1, "verification")])
                with self.assertRaises(subprocess.CalledProcessError):
                    self.build(run=run)
                self.assertEqual(self.output.read_bytes(), b"previous installer")
                self.assertEqual(list(self.root.glob(".pixelkit-dmg-*")), [])

    def test_rejects_output_inside_signed_bundle_without_running_packaging(self):
        with self.assertRaisesRegex(ValueError, "inside the app"):
            self.build(output=self.app / "installer.dmg")
        self.assertFalse((self.app / "installer.dmg").exists())

    def test_rejects_symlinked_output_without_replacing_its_target(self):
        link = self.root / "linked.dmg"
        link.symlink_to(self.output)
        with self.assertRaisesRegex(ValueError, "regular .dmg"):
            self.build(output=link)
        self.assertEqual(self.output.read_bytes(), b"previous installer")


if __name__ == "__main__":
    unittest.main()
