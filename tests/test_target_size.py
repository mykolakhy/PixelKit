from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pixelkit.app import BatchWorker
from pixelkit.runtime import ProcessingCancelled, find_magick, run_magick
from pixelkit.target_size import compress_to_size


class TargetSizeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "input.png"
        self.source.write_bytes(b"source")
        self.output = self.root / "output.webp"
        self.command = ["magick", str(self.source), "-quality", "82", str(self.output)]

    def encoder(self, command, **kwargs):
        quality = int(command[command.index("-quality") + 1])
        Path(command[-1]).write_bytes(b"x" * (100 + quality * 10))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    def test_search_keeps_the_best_fitting_trial_and_applies_lossy_webp(self):
        with patch("pixelkit.target_size.run_magick", side_effect=self.encoder) as encode:
            quality = compress_to_size(self.command, self.output, 500, lambda: False)
        self.assertEqual(quality, 40)
        self.assertEqual(self.output.stat().st_size, 500)
        self.assertLessEqual(encode.call_count, 9)
        for call in encode.call_args_list:
            self.assertIn("webp:lossless=false", call.args[0])
        self.assertEqual(self.command[self.command.index("-quality") + 1], "82")

    def test_already_fitting_maximum_needs_one_attempt(self):
        with patch("pixelkit.target_size.run_magick", side_effect=self.encoder) as encode:
            self.assertEqual(compress_to_size(self.command, self.output, 1000, lambda: False), 82)
        self.assertEqual(encode.call_count, 1)

    def test_unreachable_limit_preserves_existing_destination_and_cleans_trials(self):
        self.output.write_bytes(b"existing result")
        worker = BatchWorker([(self.command, self.output)], self.root, 50)
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.target_size.run_magick", side_effect=self.encoder):
            worker.run()
        self.assertEqual(self.output.read_bytes(), b"existing result")
        self.assertIn("Could not reach", reports[0].files[0].error)
        self.assertFalse(reports[0].files[0].succeeded)
        self.assertEqual(list(self.root.glob('.pixelkit-*')), [])

    def test_cancellation_during_search_preserves_outputs(self):
        self.output.write_bytes(b"existing result")
        worker = BatchWorker([(self.command, self.output)], self.root, 500)
        reports = []
        worker.finished.connect(reports.append)

        def encode(command, **kwargs):
            worker.cancel()
            raise ProcessingCancelled()

        with patch("pixelkit.target_size.run_magick", side_effect=encode):
            worker.run()
        self.assertTrue(reports[0].cancelled)
        self.assertEqual(self.output.read_bytes(), b"existing result")
        self.assertEqual(list(self.root.glob('.pixelkit-*')), [])

    def test_unsupported_formats_are_rejected(self):
        with self.assertRaises(ValueError):
            compress_to_size(self.command, self.root / "output.png", 500, lambda: False)

    def test_real_jpg_webp_and_avif_outputs_fit_without_changing_dimensions(self):
        magick = find_magick()
        if not magick:
            self.skipTest("ImageMagick is required")
        run_magick([magick, "-seed", "42", "-size", "128x96", "plasma:fractal", str(self.source)], check=True)
        for extension in ("jpg", "webp", "avif"):
            with self.subTest(extension=extension):
                output = self.root / f"result.{extension}"
                command = [magick, str(self.source), "-strip", "-quality", "82", str(output)]
                probe = self.root / f"probe.{extension}"
                run_magick([magick, str(self.source), "-strip", "-quality", "1", str(probe)], check=True)
                limit = probe.stat().st_size + 100
                worker = BatchWorker([(command, output)], self.root, limit)
                reports = []
                worker.finished.connect(reports.append)
                worker.run()
                self.assertTrue(reports[0].files[0].succeeded, reports[0].files[0].error)
                self.assertLessEqual(output.stat().st_size, limit)
                self.assertIsNotNone(reports[0].files[0].quality)
                dimensions = run_magick([magick, "identify", "-format", "%wx%h", str(output)], capture_output=True, text=True, check=True)
                self.assertEqual(dimensions.stdout, "128x96")


if __name__ == "__main__":
    unittest.main()
