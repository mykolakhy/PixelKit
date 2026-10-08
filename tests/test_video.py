from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event, Timer
from unittest.mock import patch

from pixelkit.runtime import ProcessingCancelled
from pixelkit.video import VideoInfo, VideoSettings, VideoWorker, encode_video, find_ffmpeg, find_ffprobe, probe_video, video_command


class VideoTestBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def run_worker(self, jobs, settings=None):
        worker = VideoWorker(jobs, self.root, settings or VideoSettings())
        reports = []
        worker.finished.connect(reports.append)
        worker.run()
        return reports[0]

    def metadata(self, **video_values):
        return {
            "streams": [{"index": 0, "codec_type": "video", "width": 1920, "height": 1080, "duration": "3.0", **video_values}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "3.0"},
        }


class VideoTests(VideoTestBase):
    def test_settings_reject_invalid_and_untyped_values(self):
        for values in ({"preset": "unknown"}, {"preset": []}, {"max_height": 1440}, {"max_height": False}, {"audio": "unknown"}, {"audio": []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                VideoSettings(**values)

    def test_probe_rejects_hdr_and_wide_gamut_instead_of_changing_colours(self):
        for video in ({"color_transfer": "smpte2084"}, {"color_transfer": "arib-std-b67"}, {"color_primaries": "bt2020"}, {"color_primaries": "smpte431"}, {"color_primaries": "smpte432"}, {"side_data_list": [{"side_data_type": "DOVI configuration record"}]}):
            result = subprocess.CompletedProcess([], 0, json.dumps(self.metadata(**video)), "")
            with self.subTest(video=video), patch("pixelkit.video._run_captured", return_value=result), self.assertRaisesRegex(ValueError, "HDR.*SDR"):
                probe_video(self.root / "camera.mov", "ffprobe")

    def test_probe_rejects_alpha_and_paletted_pixel_formats(self):
        for pixel_format in ("rgba", "argb", "bgra", "abgr", "rgba64le", "rgbaf32le", "yuva420p", "yuva444p10le", "gbrap", "gbrapf16be", "ya8", "ya16le", "yaf32be", "ayuv64le", "vuya", "uyva", "pal8"):
            result = subprocess.CompletedProcess([], 0, json.dumps(self.metadata(pix_fmt=pixel_format)), "")
            with self.subTest(pixel_format=pixel_format), patch("pixelkit.video._run_captured", return_value=result), self.assertRaisesRegex(ValueError, "transparency.*opaque"):
                probe_video(self.root / "transparent.mov", "ffprobe")

    def test_probe_accepts_opaque_sdr_colour_and_pixel_formats(self):
        for pixel_format in ("rgb24", "bgr24", "yuv420p", "yuv444p10le", "gbrp", "gray", "0rgb", "rgb0"):
            result = subprocess.CompletedProcess([], 0, json.dumps(self.metadata(pix_fmt=pixel_format, color_primaries="bt709", color_transfer="bt709")), "")
            with self.subTest(pixel_format=pixel_format), patch("pixelkit.video._run_captured", return_value=result):
                self.assertEqual(probe_video(self.root / "opaque.mov", "ffprobe").duration, 3)

    def test_probe_requires_a_real_video_stream_and_known_duration(self):
        for metadata in ({"streams": [], "format": {}}, {"streams": [{"codec_type": "audio"}], "format": {}}, self.metadata(disposition={"attached_pic": 1}), self.metadata(duration="NaN"), {"streams": "invalid", "format": {}}):
            result = subprocess.CompletedProcess([], 0, json.dumps(metadata), "")
            with self.subTest(metadata=metadata), patch("pixelkit.video._run_captured", return_value=result), self.assertRaises(ValueError):
                probe_video(self.root / "camera.mp4", "ffprobe")

    def test_keep_audio_copies_compatible_tracks_and_encodes_incompatible_tracks(self):
        command = video_command("ffmpeg", self.root / "input.mov", self.root / "out.mp4", VideoSettings(audio="keep"), VideoInfo(2, 0, 321, 181, ("aac", "pcm_s16le")))
        self.assertEqual(command[command.index("-c:a:0") + 1], "copy")
        self.assertEqual(command[command.index("-c:a:1") + 1], "aac")
        self.assertEqual(command[command.index("-b:a:1") + 1], "192k")

    def test_cancelled_batch_preserves_completed_and_existing_outputs(self):
        jobs = []
        for index in range(3):
            source = self.root / f"source {index}.mov"
            source.write_bytes(b"large original" * 100)
            output = self.root / f"result {index}.mp4"
            jobs.append((source, output))
        jobs[1][1].write_bytes(b"existing result")
        worker = VideoWorker(jobs, self.root, VideoSettings())
        reports = []
        worker.finished.connect(reports.append)
        calls = []

        def encode(command, duration, cancelled, progress):
            calls.append(command)
            Path(command[-1]).write_bytes(b"compressed output")
            if len(calls) == 2:
                worker.cancel()
                raise ProcessingCancelled()

        with patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video.find_ffprobe", return_value="ffprobe"), patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D libx264 encoder", "")), patch("pixelkit.video.probe_video", return_value=VideoInfo(2, 0, 320, 180, ())), patch("pixelkit.video.encode_video", side_effect=encode):
            worker.run()
        report = reports[0]
        self.assertTrue(report.cancelled)
        self.assertEqual([file.status for file in report.files], ["Done", "Cancelled", "Skipped"])
        self.assertEqual(jobs[0][1].read_bytes(), b"compressed output")
        self.assertEqual(jobs[1][1].read_bytes(), b"existing result")
        self.assertFalse(jobs[2][1].exists())
        self.assertTrue(all(file.media_type == "video" for file in report.files))
        self.assertGreaterEqual(report.files[0].elapsed_seconds, 0)
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_cancel_before_start_skips_without_starting_a_process(self):
        worker = VideoWorker([(self.root / "in.mov", self.root / "out.mp4")], self.root, VideoSettings())
        worker.cancel()
        reports = []
        worker.finished.connect(reports.append)
        with patch("pixelkit.video._run_captured") as process:
            worker.run()
        process.assert_not_called()
        self.assertEqual(reports[0].files[0].status, "Skipped")

    def test_outputs_cannot_overwrite_any_source_even_through_a_hard_link(self):
        first, second = self.root / "first.mp4", self.root / "second.mp4"
        first.write_bytes(b"first original")
        second.write_bytes(b"second original")
        alias = self.root / "alias.mp4"
        alias.hardlink_to(first)
        for jobs in ([(first, first)], [(first, second), (second, self.root / "safe.mp4")], [(first, alias)]):
            with self.subTest(jobs=jobs), patch("pixelkit.video._run_captured") as process:
                report = self.run_worker(jobs)
                self.assertIn("replace an original", report.files[0].error)
        self.assertEqual(first.read_bytes(), b"first original")
        self.assertEqual(second.read_bytes(), b"second original")

    def test_missing_encoder_returns_a_clear_error(self):
        source = self.root / "in.mov"
        source.write_bytes(b"original")
        with patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video.find_ffprobe", return_value="ffprobe"), patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D h264_videotoolbox encoder", "")):
            report = self.run_worker([(source, self.root / "out.mp4")])
        self.assertIn("libx264", report.files[0].error)

    def test_case_only_duplicate_destinations_are_rejected(self):
        first, second = self.root / "first.mov", self.root / "second.mov"
        first.write_bytes(b"original one")
        second.write_bytes(b"original two")
        with patch("pixelkit.video._run_captured") as process:
            report = self.run_worker([(first, self.root / "CLIP.mp4"), (second, self.root / "clip.mp4")])
        process.assert_not_called()
        self.assertTrue(all("same output path" in file.error for file in report.files))

    def test_larger_output_is_discarded_and_existing_destination_is_preserved(self):
        source, output = self.root / "in.mov", self.root / "out.mp4"
        source.write_bytes(b"original")
        output.write_bytes(b"existing output")

        def encode(command, *args):
            Path(command[-1]).write_bytes(b"larger than original" * 10)

        with patch("pixelkit.video.find_ffmpeg", return_value="ffmpeg"), patch("pixelkit.video.find_ffprobe", return_value="ffprobe"), patch("pixelkit.video._run_captured", return_value=subprocess.CompletedProcess([], 0, " V....D libx264 encoder", "")), patch("pixelkit.video.probe_video", return_value=VideoInfo(2, 0, 320, 180, ())), patch("pixelkit.video.encode_video", side_effect=encode):
            report = self.run_worker([(source, output)])
        self.assertIn("No size reduction", report.files[0].error)
        self.assertIsNone(report.files[0].after)
        self.assertEqual(source.read_bytes(), b"original")
        self.assertEqual(output.read_bytes(), b"existing output")
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_cancel_stops_and_reaps_a_silent_process(self):
        cancelled = Event()
        timer = Timer(0.2, cancelled.set)
        processes = []
        original = subprocess.Popen

        def start(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        timer.start()
        try:
            with patch("pixelkit.video.subprocess.Popen", side_effect=start), self.assertRaises(ProcessingCancelled):
                encode_video([sys.executable, "-c", "import time; time.sleep(60)"], 60, cancelled.is_set, lambda value: None)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())

    def test_streamed_progress_uses_duration_and_waits_for_success(self):
        progress = []
        encode_video([sys.executable, "-c", "print('out_time_us=1000000'); print('out_time_us=2000000'); print('progress=end')"], 2, lambda: False, progress.append)
        self.assertEqual(progress, [50, 99])


@unittest.skipUnless(find_ffmpeg() and find_ffprobe(), "FFmpeg and ffprobe are required for real video tests")
class RealVideoTests(VideoTestBase):
    def make_video(self, path, audio=True, size="321x181"):
        command = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=12"]
        if audio:
            command.extend(("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"))
        command.extend(("-t", "1.5", "-c:v", "qtrle"))
        if audio:
            command.extend(("-c:a", "pcm_s16le"))
        command.append(str(path))
        subprocess.run(command, check=True, capture_output=True, timeout=30)

    def test_real_encode_with_spaces_reduces_size_and_preserves_original(self):
        source, output = self.root / "camera clip.mov", self.root / "optimized clip.mp4"
        self.make_video(source)
        original = hashlib.sha256(source.read_bytes()).digest()
        report = self.run_worker([(source, output)])
        self.assertTrue(report.files[0].succeeded, report.files[0].error)
        self.assertLess(output.stat().st_size, source.stat().st_size)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original)
        info = probe_video(output, find_ffprobe())
        self.assertEqual((info.width, info.height), (320, 180))
        self.assertEqual(info.audio_codecs, ("aac",))
        self.assertAlmostEqual(info.duration, 1.5, places=1)

    def test_real_remove_audio_and_do_not_upscale(self):
        source, output = self.root / "source.mov", self.root / "small.mp4"
        self.make_video(source)
        report = self.run_worker([(source, output)], VideoSettings(max_height=720, audio="remove"))
        self.assertTrue(report.files[0].succeeded, report.files[0].error)
        info = probe_video(output, find_ffprobe())
        self.assertEqual((info.width, info.height), (320, 180))
        self.assertEqual(info.audio_codecs, ())

    def test_real_rotated_video_preserves_display_orientation(self):
        base, source, output = self.root / "base.mov", self.root / "portrait.mov", self.root / "portrait.mp4"
        self.make_video(base, audio=False, size="320x180")
        subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-display_rotation", "90", "-i", str(base), "-c", "copy", str(source)], check=True, capture_output=True, timeout=30)
        report = self.run_worker([(source, output)])
        self.assertTrue(report.files[0].succeeded, report.files[0].error)
        info = probe_video(output, find_ffprobe())
        self.assertEqual((info.width, info.height), (180, 320))

    def test_real_720p_reduction_preserves_aspect_and_encodes_incompatible_audio(self):
        source, output = self.root / "tall.mov", self.root / "720.mp4"
        self.make_video(source, size="400x900")
        report = self.run_worker([(source, output)], VideoSettings(max_height=720, audio="keep"))
        self.assertTrue(report.files[0].succeeded, report.files[0].error)
        info = probe_video(output, find_ffprobe())
        self.assertEqual((info.width, info.height), (320, 720))
        self.assertEqual(info.audio_codecs, ("aac",))

    def test_real_keep_audio_preserves_aac_stream_without_reencoding(self):
        original, source, output = self.root / "original.mov", self.root / "aac.mov", self.root / "keep.mp4"
        self.make_video(original)
        subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(original), "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(source)], check=True, capture_output=True, timeout=30)
        report = self.run_worker([(source, output)], VideoSettings(audio="keep"))
        self.assertTrue(report.files[0].succeeded, report.files[0].error)

        def audio_hash(path):
            result = subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f", "hash", "-hash", "sha256", "-"], check=True, capture_output=True, timeout=30)
            return result.stdout.strip()

        self.assertEqual(audio_hash(source), audio_hash(output))

    def test_real_variable_frame_rate_does_not_duplicate_or_drop_frames(self):
        source, output = self.root / "variable.mov", self.root / "variable.mp4"
        subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=1.5", "-vf", "select='if(lt(n,15),not(mod(n,2)),not(mod(n,3)))'", "-fps_mode", "vfr", "-c:v", "qtrle", str(source)], check=True, capture_output=True, timeout=30)
        report = self.run_worker([(source, output)])
        self.assertTrue(report.files[0].succeeded, report.files[0].error)

        def timestamps(path):
            result = subprocess.run([find_ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)], check=True, capture_output=True, timeout=30)
            return [float(frame["best_effort_timestamp_time"]) for frame in json.loads(result.stdout)["frames"]]

        before, after = timestamps(source), timestamps(output)
        self.assertEqual(len(before), len(after))
        self.assertTrue(all(abs(left - right) < 0.001 for left, right in zip(before, after)))

    def test_real_bursty_frame_timing_preserves_frames_and_duration_with_and_without_size_limit(self):
        source = self.root / "screen recording.mov"
        # Six frames just 1/600 s apart followed by much longer gaps reproduce
        # timestamp collisions at the encoder's default nominal frame rate.
        subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=1", "-vf", "settb=1/600,setpts='if(lt(N,6),N,(N-5)*40)'", "-fps_mode", "passthrough", "-enc_time_base:v", "filter", "-c:v", "qtrle", str(source)], check=True, capture_output=True, timeout=30)
        original = hashlib.sha256(source.read_bytes()).digest()

        def timing(path):
            result = subprocess.run([find_ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration:stream=duration:frame=best_effort_timestamp_time", "-of", "json", str(path)], check=True, capture_output=True, timeout=30)
            data = json.loads(result.stdout)
            return [float(frame["best_effort_timestamp_time"]) for frame in data["frames"]], float(data["streams"][0]["duration"]), float(data["format"]["duration"])

        before, stream_duration, duration = timing(source)
        self.assertEqual(len(before), 30)
        self.assertLess(before[1] - before[0], 0.002)
        self.assertGreater(before[-1] - before[-2], 0.06)
        for target in (None, 7000):
            with self.subTest(target=target):
                output = self.root / f"compressed-{target}.mp4"
                with patch("pixelkit.video.encode_video", wraps=encode_video) as encoder:
                    report = self.run_worker([(source, output)], VideoSettings(audio="remove", target_bytes=target))
                self.assertTrue(report.files[0].succeeded, report.files[0].error)
                after, output_stream_duration, output_duration = timing(output)
                self.assertEqual(len(before), len(after))
                self.assertTrue(all(abs(left - right) < 0.0001 for left, right in zip(before, after)))
                self.assertAlmostEqual(output_stream_duration, stream_duration, places=4)
                self.assertAlmostEqual(output_duration, duration, places=4)
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original)
                if target is not None:
                    self.assertLessEqual(output.stat().st_size, target)
                    passes = [call.args[0][call.args[0].index("-pass:v") + 1] for call in encoder.call_args_list if "-pass:v" in call.args[0]]
                    self.assertIn("1", passes)
                    self.assertIn("2", passes)
                subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(output), "-map", "0:v:0", "-fps_mode", "passthrough", "-enc_time_base:v", "filter", "-f", "null", "-"], check=True, capture_output=True, timeout=30)
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_real_alpha_video_is_rejected_without_replacing_destination(self):
        source, output = self.root / "transparent.mov", self.root / "transparent.mp4"
        subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red@0.4:s=64x64:r=4:d=0.5,format=argb", "-c:v", "qtrle", str(source)], check=True, capture_output=True, timeout=30)
        original = source.read_bytes()
        output.write_bytes(b"existing destination")
        report = self.run_worker([(source, output)])
        self.assertFalse(report.files[0].succeeded)
        self.assertIn("transparency", report.files[0].error)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(output.read_bytes(), b"existing destination")
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])

    def test_failure_continues_to_next_video_without_publishing_partial_file(self):
        bad, source = self.root / "bad.mp4", self.root / "good.mov"
        bad.write_bytes(b"not a video")
        self.make_video(source, audio=False)
        report = self.run_worker([(bad, self.root / "bad-output.mp4"), (source, self.root / "good-output.mp4")])
        self.assertFalse(report.files[0].succeeded)
        self.assertTrue(report.files[1].succeeded, report.files[1].error)
        self.assertFalse((self.root / "bad-output.mp4").exists())
        self.assertEqual(list(self.root.glob(".pixelkit-video-*")), [])


if __name__ == "__main__":
    unittest.main()
