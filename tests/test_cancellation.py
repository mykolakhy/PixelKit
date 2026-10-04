from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event, Timer
from types import SimpleNamespace
from unittest.mock import patch

from pixelkit.app import BatchWorker
from pixelkit.runtime import ProcessingCancelled, run_magick


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_cancel_keeps_completed_and_existing_files_and_removes_partial_output(self):
        jobs = []
        for index in range(3):
            source = self.root / f"input {index}.png"
            source.write_bytes(b"source")
            output = self.root / f"output {index}.webp"
            jobs.append((["magick", str(source), str(output)], output))
        jobs[1][1].write_bytes(b"previous output")
        worker = BatchWorker(jobs, self.root)
        reports = []
        worker.finished.connect(reports.append)
        calls = []

        def convert(command, **kwargs):
            calls.append(command)
            Path(command[-1]).write_bytes(b"new output")
            if len(calls) == 2:
                worker.cancel()
                raise ProcessingCancelled()
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with patch("pixelkit.app.run_magick", side_effect=convert):
            worker.run()
        report = reports[0]
        self.assertTrue(report.cancelled)
        self.assertEqual([file.status for file in report.files], ["Done", "Cancelled", "Skipped"])
        self.assertEqual(jobs[0][1].read_bytes(), b"new output")
        self.assertEqual(jobs[1][1].read_bytes(), b"previous output")
        self.assertFalse(jobs[2][1].exists())
        self.assertEqual(list(self.root.glob('.pixelkit-*')), [])
        self.assertEqual(report.after, len(b"new output"))
        self.assertEqual(len(calls), 2)

    def test_cancel_before_start_skips_every_file_without_running_a_process(self):
        worker = BatchWorker([(["magick", str(self.root / "input.png"), str(self.root / "output.webp")], self.root / "output.webp")], self.root)
        worker.cancel()
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.app.run_magick") as run:
            worker.run()
        run.assert_not_called()
        self.assertEqual(reports[0].files[0].status, "Skipped")

    def test_missing_expected_output_does_not_publish_an_empty_file(self):
        source = self.root / "animated.gif"
        source.write_bytes(b"source")
        output = self.root / "result.png"
        output.write_bytes(b"existing result")
        worker = BatchWorker([(["magick", str(source), str(output)], output)], self.root)
        reports = []
        worker.finished.connect(reports.append)

        def convert(command, **kwargs):
            temporary = Path(command[-1])
            temporary.with_name("result-0.png").write_bytes(b"frame")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with patch("pixelkit.app.run_magick", side_effect=convert):
            worker.run()
        self.assertFalse(reports[0].files[0].succeeded)
        self.assertEqual(output.read_bytes(), b"existing result")
        self.assertEqual(list(self.root.glob('.pixelkit-*')), [])

    def test_active_process_is_stopped_and_reaped_when_cancelled(self):
        cancelled = Event()
        timer = Timer(0.2, cancelled.set)
        processes = []
        original_popen = subprocess.Popen

        def start(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        timer.start()
        try:
            with patch("pixelkit.runtime.subprocess.Popen", side_effect=start):
                with self.assertRaises(ProcessingCancelled):
                    run_magick([sys.executable, "-c", "import time; time.sleep(60)"], capture_output=True, text=True, timeout=10, cancel_requested=cancelled.is_set)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())

    def test_timeout_still_applies_to_cancellable_processes(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_magick([sys.executable, "-c", "import time; time.sleep(60)"], capture_output=True, text=True, timeout=0.2, cancel_requested=lambda: False)


if __name__ == "__main__":
    unittest.main()
