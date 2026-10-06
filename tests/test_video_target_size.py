from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from pixelkit.runtime import ProcessingCancelled
from pixelkit.video import VideoInfo, VideoSettings, VideoWorker, encode_to_target, encode_video, find_ffmpeg, find_ffprobe, probe_video, video_command


def command_value(command, *options):
    for option in options:
        if option in command:
            return command[command.index(option) + 1]
    return None


class TargetVideoTestBase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pixelkit target tests ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source clip.mov"
        self.output = self.root / "result clip.mp4"
        self.source.write_bytes(b"original" * 50000)
        self.info = VideoInfo(10, 0, 320, 180, ())

    def run_worker(self, jobs, settings, info=None, encoder=None):
        worker = VideoWorker(jobs, self.root, settings)
        reports, progress = [], []
        worker.finished.connect(reports.append)
        worker.encoding_progress.connect(lambda value, name: progress.append((value, name)))
        with ExitStack() as stack:
            stack.enter_context(patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"))
            stack.enter_context(patch("pixelkit.video.find_ffprobe", return_value="ffprobe"))
            stack.enter_context(patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D libx264 encoder", "")))
            stack.enter_context(patch("pixelkit.video.probe_video", return_value=info or self.info))
            if encoder is not None:
                stack.enter_context(patch("pixelkit.video.encode_video", side_effect=encoder))
            worker.run()
        return reports[0], progress


class TargetVideoTests(TargetVideoTestBase):
    def test_target_accepts_only_positive_integer_bytes_or_none(self):
        for value in (0, -1, False, True, 1.0, "1000000", [], float("inf")):
            with self.subTest(target=value), self.assertRaisesRegex(ValueError, "positive.*size limit"):
                VideoSettings(target_bytes=value)
        for value in (None, 1, 1_000_000):
            with self.subTest(target=value):
                self.assertEqual(VideoSettings(target_bytes=value).target_bytes, value)

    def test_already_fitting_selected_preset_does_not_run_extra_passes(self):
        calls, progress = [], []

        def encode(command, duration, cancelled, update):
            calls.append(command)
            Path(command[-1]).write_bytes(b"v" * 12000)
            update(0)
            update(50)
            update(99)

        with patch("pixelkit.video.encode_video", side_effect=encode):
            encode_to_target("ffmpeg", self.source, self.output, VideoSettings(preset="high", target_bytes=12000), self.info, lambda: False, progress.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(command_value(calls[0], "-crf"), "20")
        self.assertIsNone(command_value(calls[0], "-pass", "-pass:v"))
        self.assertEqual(self.output.stat().st_size, 12000)
        self.assertEqual(progress, sorted(progress))
        self.assertLess(max(progress), 100)

    def test_unlimited_mode_keeps_one_preset_encode(self):
        calls, progress = [], []

        def encode(command, duration, cancelled, update):
            calls.append(command)
            Path(command[-1]).write_bytes(b"encoded")
            update(50)
            update(99)

        with patch("pixelkit.video.encode_video", side_effect=encode):
            encode_to_target("ffmpeg", self.source, self.output, VideoSettings(), self.info, lambda: False, progress.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(progress, [50, 99])
        self.assertIsNone(command_value(calls[0], "-b:v"))

    def test_tight_cap_uses_two_passes_with_no_audio_first_pass_and_private_logs(self):
        calls, progress = [], []
        info = VideoInfo(10, 0, 320, 180, ("aac",))

        def encode(command, duration, cancelled, update):
            calls.append(command)
            pass_number = command_value(command, "-pass", "-pass:v")
            if command_value(command, "-c:v") is None:
                Path(command[-1]).write_bytes(b"a" * 18000)
            elif pass_number != "1":
                Path(command[-1]).write_bytes(b"v" * (160000 if pass_number is None else 95000))
            for value in (0, 50, 99):
                update(value)

        with patch("pixelkit.video.encode_video", side_effect=encode):
            encode_to_target("ffmpeg", self.source, self.output, VideoSettings(audio="keep", target_bytes=100000), info, lambda: False, progress.append)
        passes = [command for command in calls if command_value(command, "-pass", "-pass:v")]
        self.assertEqual([command_value(command, "-pass", "-pass:v") for command in passes], ["1", "2"])
        self.assertGreater(int(command_value(passes[0], "-b:v")), 0)
        self.assertEqual(command_value(passes[0], "-b:v"), command_value(passes[1], "-b:v"))
        self.assertNotIn("-crf", passes[0])
        self.assertIn("-an", passes[0])
        self.assertEqual(command_value(passes[0], "-f"), "null")
        self.assertEqual(passes[0][-1], os.devnull)
        self.assertEqual(command_value(passes[1], "-c:a:0"), "copy")
        logs = [Path(command_value(command, "-passlogfile")) for command in passes]
        self.assertEqual(logs[0], logs[1])
        self.assertTrue(all(log.is_absolute() and log.parent == self.output.parent for log in logs))
        audio_command = next(command for command in calls if command_value(command, "-c:v") is None)
        self.assertEqual(command_value(audio_command, "-c:a"), "copy")
        self.assertEqual(command_value(audio_command, "-map"), "0:a")
        self.assertTrue(Path(audio_command[-1]).is_relative_to(self.output.parent))
        self.assertLessEqual(self.output.stat().st_size, 100000)
        self.assertEqual(progress, sorted(progress))
        self.assertLess(max(progress), 100)

    def test_observed_overshoot_retries_both_passes_at_a_lower_bitrate(self):
        calls, progress = [], []
        second_passes = 0

        def encode(command, duration, cancelled, update):
            nonlocal second_passes
            calls.append(command)
            pass_number = command_value(command, "-pass", "-pass:v")
            if pass_number is None:
                Path(command[-1]).write_bytes(b"v" * 160000)
            elif pass_number == "2":
                second_passes += 1
                Path(command[-1]).write_bytes(b"v" * (105000 if second_passes == 1 else 99000))
            for value in (0, 50, 99):
                update(value)

        with patch("pixelkit.video.encode_video", side_effect=encode):
            encode_to_target("ffmpeg", self.source, self.output, VideoSettings(target_bytes=100000), self.info, lambda: False, progress.append)
        passes = [command for command in calls if command_value(command, "-pass", "-pass:v")]
        self.assertEqual([command_value(command, "-pass", "-pass:v") for command in passes], ["1", "2", "1", "2"])
        bitrates = [int(command_value(command, "-b:v")) for command in passes]
        self.assertEqual(bitrates[0], bitrates[1])
        self.assertEqual(bitrates[2], bitrates[3])
        self.assertLess(bitrates[2], bitrates[0])
        self.assertNotEqual(command_value(passes[0], "-passlogfile"), command_value(passes[2], "-passlogfile"))
        self.assertLessEqual(self.output.stat().st_size, 100000)
        self.assertEqual(progress, sorted(progress))
        self.assertLess(max(progress), 100)

    def test_audio_below_cap_gets_a_minimum_bitrate_trial_when_reserve_exhausts_budget(self):
        calls = []
        original = self.source.read_bytes()

        def encode(command, duration, cancelled, update):
            calls.append(command)
            pass_number = command_value(command, "-pass", "-pass:v")
            if command_value(command, "-c:v") is None:
                Path(command[-1]).write_bytes(b"a" * 9500)
            elif pass_number != "1":
                Path(command[-1]).write_bytes(b"v" * (12000 if pass_number is None else 9900))
            update(99)

        report, progress = self.run_worker([(self.source, self.output)], VideoSettings(audio="keep", target_bytes=10000), VideoInfo(10, 0, 320, 180, ("aac",)), encode)
        self.assertTrue(report.files[0].succeeded, report.files[0].error)
        passes = [command for command in calls if command_value(command, "-pass", "-pass:v")]
        self.assertEqual([command_value(command, "-pass", "-pass:v") for command in passes], ["1", "2"])
        self.assertEqual([command_value(command, "-b:v") for command in passes], ["1000", "1000"])
        self.assertEqual(command_value(passes[1], "-c:a:0"), "copy")
        self.assertEqual(self.output.stat().st_size, 9900)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])
        self.assertEqual([value for value, name in progress], sorted(value for value, name in progress))
        self.assertEqual(progress[-1][0], 100)

    def test_correction_below_minimum_tries_minimum_once_then_checks_actual_cap(self):
        for final_size in (1999, 2001):
            with self.subTest(final_size=final_size):
                calls = []
                self.output.write_bytes(b"existing destination")
                original = self.source.read_bytes()

                def encode(command, duration, cancelled, update):
                    calls.append(command)
                    pass_number = command_value(command, "-pass", "-pass:v")
                    if pass_number is None:
                        Path(command[-1]).write_bytes(b"v" * 3000)
                    elif pass_number == "2":
                        size = final_size if command_value(command, "-b:v") == "1000" else 2500
                        Path(command[-1]).write_bytes(b"v" * size)
                    update(99)

                report, progress = self.run_worker([(self.source, self.output)], VideoSettings(audio="remove", target_bytes=2000), encoder=encode)
                passes = [command for command in calls if command_value(command, "-pass", "-pass:v")]
                self.assertEqual([command_value(command, "-pass", "-pass:v") for command in passes], ["1", "2", "1", "2"])
                self.assertEqual([command_value(command, "-b:v") for command in passes], ["1200", "1200", "1000", "1000"])
                if final_size <= 2000:
                    self.assertTrue(report.files[0].succeeded, report.files[0].error)
                    self.assertEqual(self.output.stat().st_size, final_size)
                    self.assertEqual(progress[-1][0], 100)
                else:
                    self.assertFalse(report.files[0].succeeded)
                    self.assertIn("Cannot fit", report.files[0].error)
                    self.assertIsNone(report.files[0].after)
                    self.assertEqual(self.output.read_bytes(), b"existing destination")
                    self.assertNotIn(100, [value for value, name in progress])
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])
                self.assertEqual([value for value, name in progress], sorted(value for value, name in progress))

    def test_impossible_audio_budget_or_limit_preserves_original_and_existing_output(self):
        for audio, cap in (("keep", 10000), ("remove", 100)):
            with self.subTest(audio=audio, cap=cap):
                self.output.write_bytes(b"existing destination")
                original = self.source.read_bytes()
                calls = []

                def encode(command, duration, cancelled, update):
                    calls.append(command)
                    if command_value(command, "-pass", "-pass:v") != "1":
                        Path(command[-1]).write_bytes(b"v" * (12000 if command_value(command, "-c:v") else 10000))

                report, progress = self.run_worker([(self.source, self.output)], VideoSettings(audio=audio, target_bytes=cap), VideoInfo(10, 0, 320, 180, ("aac",)), encode)
                self.assertFalse(report.files[0].succeeded)
                self.assertIn("Cannot fit", report.files[0].error)
                self.assertIn("Compress/Remove audio", report.files[0].error)
                self.assertEqual(report.files[0].target_bytes, cap)
                self.assertIsNone(report.files[0].after)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual(self.output.read_bytes(), b"existing destination")
                passes = [command for command in calls if command_value(command, "-pass", "-pass:v")]
                self.assertEqual([command_value(command, "-pass", "-pass:v") for command in passes], [] if audio == "keep" else ["1", "2"])
                self.assertTrue(all(command_value(command, "-b:v") == "1000" for command in passes))
                self.assertNotIn(100, [value for value, name in progress])
                self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_repeated_overshoot_is_bounded_and_never_published(self):
        self.output.write_bytes(b"existing destination")
        calls = []

        def encode(command, duration, cancelled, update):
            calls.append(command)
            if command_value(command, "-pass", "-pass:v") != "1":
                Path(command[-1]).write_bytes(b"v" * 105000)

        report, progress = self.run_worker([(self.source, self.output)], VideoSettings(target_bytes=100000), encoder=encode)
        self.assertIn("Cannot fit", report.files[0].error)
        self.assertLessEqual(len(calls), 11)
        self.assertEqual(self.output.read_bytes(), b"existing destination")
        self.assertNotIn(100, [value for value, name in progress])
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_worker_checks_actual_cap_again_before_replacing_destination(self):
        self.output.write_bytes(b"existing destination")
        original = self.source.read_bytes()

        def oversized(ffmpeg, source, output, settings, info, cancelled, progress):
            output.write_bytes(b"v" * 10001)

        with patch("pixelkit.video.encode_to_target", side_effect=oversized):
            report, progress = self.run_worker([(self.source, self.output)], VideoSettings(target_bytes=10000))
        self.assertIn("Cannot fit", report.files[0].error)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), b"existing destination")
        self.assertNotIn(100, [value for value, name in progress])
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_cancellation_during_every_encoding_stage_keeps_finished_files_and_skips_rest(self):
        for cancel_stage in ("preset", "audio", "pass1", "pass2", "retry"):
            with self.subTest(stage=cancel_stage):
                jobs = []
                for index in range(3):
                    source = self.root / f"{cancel_stage} source {index}.mov"
                    output = self.root / f"{cancel_stage} result {index}.mp4"
                    source.write_bytes(b"original" * 50000)
                    jobs.append((source, output))
                jobs[1][1].write_bytes(b"existing destination")
                originals = [source.read_bytes() for source, output in jobs]
                worker = VideoWorker(jobs, self.root, VideoSettings(audio="keep", target_bytes=100000))
                reports, progress, pass_logs = [], [], []
                worker.finished.connect(reports.append)
                worker.encoding_progress.connect(lambda value, name: progress.append((value, name)))
                first_pass_count = 0

                def encode(command, duration, cancelled, update):
                    nonlocal first_pass_count
                    input_path = Path(command_value(command, "-i"))
                    pass_number = command_value(command, "-pass", "-pass:v")
                    if input_path == jobs[0][0]:
                        Path(command[-1]).write_bytes(b"completed output")
                        update(99)
                        return
                    if command_value(command, "-c:v") is None:
                        stage, size = "audio", 20000
                    elif pass_number == "1":
                        first_pass_count += 1
                        stage, size = ("pass1" if first_pass_count == 1 else "retry"), 0
                        log = Path(command_value(command, "-passlogfile") + "-0.log")
                        log.write_bytes(b"private encoder statistics")
                        pass_logs.append(log)
                    elif pass_number == "2":
                        stage, size = "pass2", 105000
                    else:
                        stage, size = "preset", 160000
                    if size:
                        Path(command[-1]).write_bytes(b"v" * size)
                    update(50)
                    if stage == cancel_stage:
                        worker.cancel()
                        raise ProcessingCancelled()

                with patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video.find_ffprobe", return_value="ffprobe"), patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D libx264 encoder", "")), patch("pixelkit.video.probe_video", return_value=VideoInfo(10, 0, 320, 180, ("aac",))), patch("pixelkit.video.encode_video", side_effect=encode):
                    worker.run()
                report = reports[0]
                self.assertTrue(report.cancelled)
                self.assertEqual([file.status for file in report.files], ["Done", "Cancelled", "Skipped"])
                self.assertTrue(all(file.target_bytes == 100000 for file in report.files))
                self.assertEqual([source.read_bytes() for source, output in jobs], originals)
                self.assertEqual(jobs[0][1].read_bytes(), b"completed output")
                self.assertEqual(jobs[1][1].read_bytes(), b"existing destination")
                self.assertFalse(jobs[2][1].exists())
                self.assertNotIn((100, jobs[1][0].name), progress)
                self.assertTrue(all(not log.exists() for log in pass_logs))
                self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_cancel_before_start_keeps_target_metadata_without_encoding(self):
        worker = VideoWorker([(self.source, self.output)], self.root, VideoSettings(target_bytes=50000))
        reports = []
        worker.finished.connect(reports.append)
        worker.cancel()
        with patch("pixelkit.video.encode_to_target") as encode:
            worker.run()
        encode.assert_not_called()
        self.assertEqual(reports[0].files[0].status, "Skipped")
        self.assertEqual(reports[0].files[0].target_bytes, 50000)

    def test_completion_progress_is_emitted_only_after_atomic_publication(self):
        self.output.write_bytes(b"existing destination")
        worker = VideoWorker([(self.source, self.output)], self.root, VideoSettings(target_bytes=100000))
        observations, reports = [], []
        worker.encoding_progress.connect(lambda value, name: observations.append((value, self.output.read_bytes())))
        worker.finished.connect(reports.append)
        second_passes = 0

        def encode(command, duration, cancelled, update):
            nonlocal second_passes
            pass_number = command_value(command, "-pass", "-pass:v")
            if pass_number != "1":
                if pass_number == "2":
                    second_passes += 1
                size = 160000 if pass_number is None else (105000 if second_passes == 1 else 99000)
                Path(command[-1]).write_bytes(b"v" * size)
            for value in (0, 50, 99):
                update(value)

        with patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video.find_ffprobe", return_value="ffprobe"), patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D libx264 encoder", "")), patch("pixelkit.video.probe_video", return_value=self.info), patch("pixelkit.video.encode_video", side_effect=encode):
            worker.run()
        self.assertTrue(reports[0].files[0].succeeded, reports[0].files[0].error)
        values = [value for value, contents in observations]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)
        self.assertTrue(all(contents == b"existing destination" for value, contents in observations[:-1]))
        self.assertEqual(len(observations[-1][1]), 99000)
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])


@unittest.skipUnless(find_ffmpeg() and find_ffprobe(), "FFmpeg and ffprobe are required for real target-size tests")
class RealTargetVideoTests(TargetVideoTestBase):
    def make_video(self, audio, size="320x180", rate=12, duration=2):
        command = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}"]
        if audio:
            command.extend(("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2"))
        command.extend(("-c:v", "qtrle", "-pix_fmt", "rgb24"))
        if audio:
            command.extend(("-c:a", "aac", "-b:a", "96k"))
        command.append(str(self.source))
        subprocess.run(command, check=True, capture_output=True, timeout=30)

    def real_worker(self, settings):
        worker = VideoWorker([(self.source, self.output)], self.root, settings)
        reports = []
        worker.finished.connect(reports.append)
        worker.run()
        return reports[0]

    def audio_hash(self, path):
        result = subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f", "hash", "-hash", "sha256", "-"], check=True, capture_output=True, timeout=30)
        return result.stdout.strip()

    def check_real_limit(self, audio_mode, cap):
        self.make_video(audio_mode != "remove")
        source_hash = hashlib.sha256(self.source.read_bytes()).digest()
        info = probe_video(self.source, find_ffprobe())
        baseline = self.root / "preset baseline.mp4"
        command = video_command(find_ffmpeg(), self.source, baseline, VideoSettings(audio=audio_mode), info)
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        self.assertGreater(baseline.stat().st_size, cap, "Fixture must exercise constrained encoding, rather than the already-fitting preset path")
        report = self.real_worker(VideoSettings(audio=audio_mode, target_bytes=cap))
        file = report.files[0]
        self.assertTrue(file.succeeded, file.error)
        self.assertEqual(file.target_bytes, cap)
        self.assertEqual(file.after, self.output.stat().st_size)
        self.assertLessEqual(file.after, cap)
        self.assertLess(file.after, file.before)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), source_hash)
        result_info = probe_video(self.output, find_ffprobe())
        self.assertEqual((result_info.width, result_info.height), (320, 180))
        self.assertEqual(result_info.audio_codecs, () if audio_mode == "remove" else ("aac",))
        self.assertAlmostEqual(result_info.duration, info.duration, places=1)
        subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(self.output), "-f", "null", os.devnull], check=True, capture_output=True, timeout=30)
        if audio_mode == "keep":
            self.assertEqual(self.audio_hash(self.source), self.audio_hash(self.output))
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_real_silent_video_with_spaces_meets_byte_cap_and_preserves_dimensions(self):
        self.check_real_limit("remove", 18000)

    def test_real_video_with_compressed_audio_meets_byte_cap_and_decodes(self):
        self.check_real_limit("compress", 45000)

    def test_real_video_keeps_compatible_audio_bit_for_bit_within_cap(self):
        self.check_real_limit("keep", 45000)

    def test_real_audio_near_cap_still_fits_with_minimum_bitrate_and_keeps_all_frames(self):
        ffmpeg, ffprobe = find_ffmpeg(), find_ffprobe()
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=60", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=60", "-c:v", "qtrle", "-pix_fmt", "rgb24", "-c:a", "aac", "-b:a", "128k", str(self.source)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        source_hash = hashlib.sha256(self.source.read_bytes()).digest()
        info = probe_video(self.source, ffprobe)
        settings = VideoSettings(audio="keep")
        baseline = self.root / "audio-heavy preset.mp4"
        subprocess.run(video_command(ffmpeg, self.source, baseline, settings, info), check=True, capture_output=True, timeout=30)
        witness = self.root / "minimum bitrate witness.mp4"
        for pass_number in (1, 2):
            subprocess.run(video_command(ffmpeg, self.source, witness, settings, info, video_bitrate=1000, pass_number=pass_number, pass_log=self.root / "witness-pass"), check=True, capture_output=True, timeout=30)
        # Round the feasible witness up to the UI's 0.001 MB precision and add
        # one further step, so differing encoder builds have a little margin.
        cap = ((witness.stat().st_size + 999) // 1000 + 1) * 1000
        audio = self.root / "retained audio budget.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(baseline), "-map", "0:a", "-vn", "-c:a", "copy", "-map_metadata", "-1", "-map_chapters", "-1", "-f", "mp4", str(audio)], check=True, capture_output=True, timeout=30)
        self.assertLess(audio.stat().st_size, cap)
        self.assertGreater(audio.stat().st_size, cap * 0.97, "Retained audio must exhaust the conservative reserve")
        self.assertGreater(baseline.stat().st_size, cap, "The selected preset must exceed the cap")
        self.output.write_bytes(b"existing destination")
        report = self.real_worker(VideoSettings(audio="keep", target_bytes=cap))
        file = report.files[0]
        self.assertTrue(file.succeeded, file.error)
        self.assertEqual(file.after, self.output.stat().st_size)
        self.assertLessEqual(file.after, cap)
        self.assertLess(file.after, file.before)
        result_info = probe_video(self.output, ffprobe)
        self.assertEqual((result_info.width, result_info.height), (64, 64))
        self.assertEqual(result_info.audio_codecs, ("aac",))
        self.assertAlmostEqual(result_info.duration, 60, places=3)
        frames = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1", str(self.output)], check=True, capture_output=True, text=True, timeout=30)
        self.assertEqual(int(frames.stdout.strip()), 300)
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(self.output), "-f", "null", os.devnull], check=True, capture_output=True, timeout=30)
        self.assertEqual(self.audio_hash(self.source), self.audio_hash(self.output))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), source_hash)
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def check_tiny_limits(self, size, caps):
        self.make_video(False, size=size, rate=10, duration=1)
        source_hash = hashlib.sha256(self.source.read_bytes()).digest()
        info = probe_video(self.source, find_ffprobe())
        baseline = self.root / "tiny preset baseline.mp4"
        subprocess.run(video_command(find_ffmpeg(), self.source, baseline, VideoSettings(audio="remove"), info), check=True, capture_output=True, timeout=30)
        for cap in caps:
            with self.subTest(target_bytes=cap):
                self.assertGreater(baseline.stat().st_size, cap)
                settings = VideoSettings(audio="remove", target_bytes=cap)
                with patch("pixelkit.video.encode_video", wraps=encode_video) as encoder:
                    report = self.real_worker(settings)
                file = report.files[0]
                self.assertTrue(file.succeeded, file.error)
                self.assertLessEqual(self.output.stat().st_size, cap)
                self.assertEqual(file.after, self.output.stat().st_size)
                self.assertEqual(file.target_bytes, cap)
                passes = [command_value(call.args[0], "-pass", "-pass:v") for call in encoder.call_args_list]
                self.assertIn("1", passes)
                self.assertIn("2", passes)
                result_info = probe_video(self.output, find_ffprobe())
                self.assertEqual((result_info.width, result_info.height), tuple(map(int, size.split("x"))))
                self.assertEqual(result_info.audio_codecs, ())
                self.assertAlmostEqual(result_info.duration, 1, places=3)
                frames = subprocess.run([find_ffprobe(), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1", str(self.output)], check=True, capture_output=True, text=True, timeout=30)
                self.assertEqual(int(frames.stdout.strip()), 10)
                subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(self.output), "-f", "null", os.devnull], check=True, capture_output=True, timeout=30)
                self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), source_hash)
                self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_real_tiny_limit_can_fit_when_smaller_than_fixed_container_reserve(self):
        self.check_tiny_limits("64x64", (2000, 2500, 3000, 3500, 4000))

    def test_real_small_video_fits_after_repeated_bitrate_corrections(self):
        self.check_tiny_limits("32x32", (2000,))


if __name__ == "__main__":
    unittest.main()
