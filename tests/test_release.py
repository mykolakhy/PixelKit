from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import publish_macos_release as release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="PixelKit release $() ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version_file = self.root / "__init__.py"
        self.version_file.write_text('__version__ = "1.13.0"\n', encoding="utf-8")
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.assets = tuple(self.artifacts / f"PixelKit-1.13.0-macOS-{arch}.dmg" for arch in ("arm64", "x86_64"))
        for asset in self.assets:
            asset.write_bytes(b"disk image")

    def test_stable_tag_must_equal_literal_source_version_without_importing_it(self):
        self.version_file.write_text('__version__ = "1.13.0"\nraise RuntimeError("must not execute")\n', encoding="utf-8")
        self.assertEqual(release.validate_tag("v1.13.0", self.version_file), "1.13.0")
        with self.assertRaisesRegex(release.ReleaseError, "does not match"):
            release.validate_tag("v1.13.1", self.version_file)

    def test_malformed_and_prerelease_tags_are_rejected(self):
        tags = ("1.13.0", "v1.13", "v1.13.0.1", "v1.13.0-beta.1", "v1.13.0+build",
                "v01.13.0", "v1.013.0", "v1.13.00", "v-1.13.0", "v１.13.0", "v1.13.0\n",
                "v1.13.0; touch injected", " v1.13.0", "v1.13.0 ")
        for tag in tags:
            with self.subTest(tag=tag), self.assertRaises(release.ReleaseError):
                release.validate_tag(tag, self.version_file)

    def test_zero_components_are_valid_but_source_leading_zeroes_are_rejected(self):
        self.version_file.write_text('__version__ = "0.0.0"\n', encoding="utf-8")
        self.assertEqual(release.validate_tag("v0.0.0", self.version_file), "0.0.0")
        self.version_file.write_text('__version__ = "01.13.0"\n', encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseError, "leading zeroes"):
            release.validate_tag("v1.13.0", self.version_file)

    def test_missing_computed_duplicate_or_invalid_source_versions_are_rejected(self):
        sources = ("", '__version__ = ".".join(["1", "13", "0"])', '__version__ = 1.13',
                   '__version__ = "1.13.0"\n__version__ = "1.13.0"', '__version__ = "1.13.0-beta"',
                   '__version__ = "1.13.0"\ninvalid syntax!')
        for source in sources:
            with self.subTest(source=source):
                self.version_file.write_text(source, encoding="utf-8")
                with self.assertRaises(release.ReleaseError):
                    release.validate_tag("v1.13.0", self.version_file)

    def test_exact_architecture_assets_are_required(self):
        self.assertEqual(release.validate_assets(self.artifacts, "1.13.0"), self.assets)
        self.assets[1].unlink()
        with self.assertRaisesRegex(release.ReleaseError, "missing"):
            release.validate_assets(self.artifacts, "1.13.0")

    def test_wrong_version_architecture_or_extra_assets_are_rejected(self):
        names = ("PixelKit-1.12.1-macOS-x86_64.dmg", "PixelKit-1.13.0-macOS-intel.dmg",
                 "PixelKit-1.13.0-macOS-universal.dmg", "PixelKit-1.13.0-macOS-arm64-copy.dmg",
                 "notes.txt")
        for name in names:
            with self.subTest(name=name):
                unexpected = self.artifacts / name
                unexpected.write_bytes(b"unexpected")
                try:
                    with self.assertRaisesRegex(release.ReleaseError, "unexpected"):
                        release.validate_assets(self.artifacts, "1.13.0")
                finally:
                    unexpected.unlink()

    def test_empty_asset_is_rejected(self):
        self.assets[0].write_bytes(b"")
        with self.assertRaisesRegex(release.ReleaseError, "nonempty regular file"):
            release.validate_assets(self.artifacts, "1.13.0")

    def test_asset_directory_or_symlink_is_rejected(self):
        self.assets[0].unlink()
        self.assets[0].mkdir()
        with self.assertRaisesRegex(release.ReleaseError, "nonempty regular file"):
            release.validate_assets(self.artifacts, "1.13.0")
        self.assets[0].rmdir()
        self.assets[0].symlink_to(self.assets[1])
        with self.assertRaisesRegex(release.ReleaseError, "not a symlink"):
            release.validate_assets(self.artifacts, "1.13.0")

    def test_missing_or_symlinked_artifact_directory_is_rejected(self):
        with self.assertRaisesRegex(release.ReleaseError, "Cannot inspect"):
            release.validate_assets(self.root / "missing", "1.13.0")
        linked = self.root / "linked artifacts"
        linked.symlink_to(self.artifacts, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, "real directory"):
            release.validate_assets(linked, "1.13.0")

    def test_publish_passes_both_assets_as_arguments_without_a_shell(self):
        with patch.object(release.subprocess, "run", return_value=subprocess.CompletedProcess(["gh"], 0, stdout="v1.12.1\n")) as run:
            release.publish_release("v1.13.0", self.artifacts, self.version_file)
        self.assertEqual(run.call_count, 2)
        lookup = run.call_args_list[0]
        self.assertEqual(lookup.args[0][:3], ["gh", "api", "repos/{owner}/{repo}/releases"])
        self.assertIn("--paginate", lookup.args[0])
        self.assertEqual(lookup.kwargs, {"check": True, "capture_output": True, "text": True})
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gh", "release", "create", "v1.13.0"])
        self.assertEqual(command[4:6], [str(asset.absolute()) for asset in self.assets])
        self.assertIn("--verify-tag", command)
        self.assertIn("--generate-notes", command)
        self.assertEqual(command[command.index("--title") + 1], "PixelKit 1.13.0")
        self.assertEqual(run.call_args.kwargs, {"check": True})
        for unsafe_option in ("--clobber", "--draft", "--prerelease", "--target"):
            self.assertNotIn(unsafe_option, command)

    def test_invalid_tag_or_artifacts_never_reach_github(self):
        with patch.object(release.subprocess, "run") as run:
            with self.assertRaises(release.ReleaseError):
                release.publish_release("v1.12.1", self.artifacts, self.version_file)
            self.assets[0].unlink()
            with self.assertRaises(release.ReleaseError):
                release.publish_release("v1.13.0", self.artifacts, self.version_file)
        run.assert_not_called()

    def test_existing_published_or_draft_release_blocks_creation(self):
        with patch.object(release.subprocess, "run", return_value=subprocess.CompletedProcess(["gh"], 0, stdout="v1.12.1\nv1.13.0\n")) as run:
            with self.assertRaisesRegex(release.ReleaseError, "already exists"):
                release.publish_release("v1.13.0", self.artifacts, self.version_file)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1], "api")

    def test_lookup_error_blocks_creation(self):
        error = subprocess.CalledProcessError(1, ["gh", "api"])
        with patch.object(release.subprocess, "run", side_effect=error) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish_release("v1.13.0", self.artifacts, self.version_file)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1], "api")

    def test_creation_error_is_not_retried_or_clobbered(self):
        error = subprocess.CalledProcessError(1, ["gh", "release", "create"], stderr="a release with the same tag name already exists")
        with patch.object(release.subprocess, "run", side_effect=[subprocess.CompletedProcess(["gh"], 0, stdout=""), error]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish_release("v1.13.0", self.artifacts, self.version_file)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][1:3], ["release", "create"])

    def test_validate_mode_prints_version_without_publication(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(release.subprocess, "run") as run:
            release.main(["--tag", "v1.13.0", "--version-file", str(self.version_file)])
        self.assertEqual(output.getvalue(), "1.13.0\n")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
