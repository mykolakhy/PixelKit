from __future__ import annotations

import copy
import unittest
from dataclasses import FrozenInstanceError

from pixelkit.updates import LATEST_RELEASE_API, RELEASES_URL, ReleaseInfo, parse_release


def installer(version="1.10.0", arch="arm64", **values):
    name = f"PixelKit-{version}-macOS-{arch}.dmg"
    asset = {
        "name": name,
        "state": "uploaded",
        "size": 123456,
        "content_type": "application/octet-stream",
        "browser_download_url": f"{RELEASES_URL}/download/v{version}/{name}",
    }
    asset.update(values)
    return asset


def release(version="1.10.0", **values):
    payload = {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "html_url": f"{RELEASES_URL}/tag/v{version}",
        "assets": [installer(version)],
    }
    payload.update(values)
    return payload


class ReleaseTests(unittest.TestCase):
    def parse(self, payload=None, current="1.9.9", *, system="Darwin", machine="arm64"):
        return parse_release(release() if payload is None else payload, current, system=system, machine=machine)

    def test_constants_and_frozen_result(self):
        self.assertEqual(LATEST_RELEASE_API, "https://api.github.com/repos/mykolakhy/PixelKit/releases/latest")
        self.assertEqual(RELEASES_URL, "https://github.com/mykolakhy/PixelKit/releases")
        info = self.parse()
        self.assertIsInstance(info, ReleaseInfo)
        with self.assertRaises(FrozenInstanceError):
            info.version = "2.0.0"

    def test_numeric_version_comparison(self):
        for published, current in (("1.10.0", "1.9.9"), ("2.0.0", "1.99.99"), ("1.0.10", "1.0.9"), ("1.1.0", "1.0.99")):
            with self.subTest(published=published, current=current):
                info = self.parse(release(published), current)
                self.assertEqual(info.version, published)
                self.assertTrue(info.is_newer)

    def test_equal_and_older_versions_are_not_updates(self):
        for current in ("1.10.0", "1.11.0", "2.0.0"):
            with self.subTest(current=current):
                self.assertFalse(self.parse(current=current).is_newer)

    def test_only_published_stable_releases_are_accepted(self):
        for field in ("draft", "prerelease"):
            for value in (True, None, "false", 0, 1):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "published stable"):
                        self.parse(release(**{field: value}))
            payload = release()
            del payload[field]
            with self.assertRaises(ValueError):
                self.parse(payload)

    def test_release_tags_are_strict_and_unambiguous(self):
        invalid = (None, 123, "1.10.0", "V1.10.0", "v1.10", "v1.10.0.1", "v01.10.0", "v1.010.0", "v1.10.00", "v1.10.0-beta.1", "v1.10.0+build", " v1.10.0", "v1.10.0\n", "v１.10.0")
        for tag in invalid:
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, "release tag"):
                    self.parse(release(tag_name=tag))

    def test_installed_versions_are_strict(self):
        for current in (None, 123, "v1.0.0", "01.0.0", "1.0", "1.0.0-beta", "1.0.0 "):
            with self.subTest(current=current):
                with self.assertRaisesRegex(ValueError, "installed version"):
                    self.parse(current=current)

    def test_selects_only_exact_installer_for_the_current_architecture(self):
        assets = [
            installer(arch="x86_64"),
            installer(arch="arm64", name="PixelKit-1.10.0-macOS-arm64.dmg.sha256"),
            installer(version="1.9.0"),
            installer(arch="arm64"),
        ]
        for machine, arch, label in (("arm64", "arm64", "Apple silicon"), ("aarch64", "arm64", "Apple silicon"), ("x86_64", "x86_64", "Intel"), ("AMD64", "x86_64", "Intel")):
            with self.subTest(machine=machine):
                info = self.parse(release(assets=assets), machine=machine)
                self.assertEqual(info.download_url, installer(arch=arch)["browser_download_url"])
                self.assertEqual(info.download_label, f"Download for macOS ({label})")

    def test_unknown_or_other_platforms_keep_the_release_page(self):
        for system, machine in (("Windows", "amd64"), ("Linux", "aarch64"), ("Darwin", "i386"), ("Darwin", "armv7"), ("Darwin", "x64"), ("Darwin", "")):
            with self.subTest(system=system, machine=machine):
                info = self.parse(system=system, machine=machine)
                self.assertIsNone(info.download_url)
                self.assertIsNone(info.download_label)
                self.assertEqual(info.page_url, f"{RELEASES_URL}/tag/v1.10.0")

    def test_python_platform_spelling_selects_mac_installer(self):
        info = self.parse(system="darwin", machine="aarch64")
        self.assertEqual(info.download_url, installer()["browser_download_url"])
        self.assertEqual(info.download_label, "Download for macOS (Apple silicon)")

    def test_missing_staged_or_empty_installer_keeps_release_page(self):
        cases = ([], [installer(arch="x86_64")], [installer(size=0)], [installer(state="new")])
        for assets in cases:
            with self.subTest(assets=assets):
                info = self.parse(release(assets=assets))
                self.assertIsNone(info.download_url)
                self.assertIsNone(info.download_label)
                self.assertTrue(info.is_newer)

    def test_filename_case_and_near_matches_are_not_guessed(self):
        for name in ("pixelkit-1.10.0-macOS-arm64.dmg", "PixelKit-1.10.0-macos-arm64.dmg", "PixelKit-1.10.0-macOS-aarch64.dmg", "PixelKit-1.10.0-macOS-universal.dmg", "PixelKit-1.10.0-macOS-arm64.zip", "PixelKit-1.10.0-macOS-arm64.dmg "):
            with self.subTest(name=name):
                self.assertIsNone(self.parse(release(assets=[installer(name=name)])).download_url)

    def test_malformed_response_shapes_are_rejected(self):
        for payload in (None, [], "{}", 1, {}, release(assets=None), release(assets={}), release(assets=[None]), release(assets=[[]]), release(assets=[{}]), release(assets=[{"name": 123}]), release(assets=[{"name": ""}])):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_release(payload, "1.0.0", system="Darwin", machine="arm64")

    def test_malformed_matching_installer_metadata_is_rejected(self):
        for values in ({"size": None}, {"size": -1}, {"size": True}, {"size": "100"}, {"state": None}, {"state": True}):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "upload details"):
                    self.parse(release(assets=[installer(**values)]))
        for field in ("size", "state", "browser_download_url"):
            asset = installer()
            del asset[field]
            with self.subTest(missing=field):
                with self.assertRaises(ValueError):
                    self.parse(release(assets=[asset]))

    def test_duplicate_matching_installers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.parse(release(assets=[installer(), installer()]))

    def test_release_page_must_be_the_canonical_repository_tag_url(self):
        canonical = f"{RELEASES_URL}/tag/v1.10.0"
        malicious = (
            None,
            canonical.replace("https://", "http://"),
            canonical.replace("github.com", "github.com.evil.test"),
            canonical.replace("github.com", "github.com@evil.test"),
            canonical.replace("github.com", "user@github.com"),
            canonical.replace("github.com", "github.com:443"),
            canonical.replace("github.com", "github.com:8443"),
            canonical.replace("mykolakhy/PixelKit", "other/PixelKit"),
            canonical.replace("/tag/v1.10.0", "/tag/v1.9.0"),
            canonical.replace("/tag/", "/tag/%2e%2e/tag/"),
            canonical + "?redirect=https://evil.test",
            canonical + "#https://evil.test",
            canonical + "/",
            " " + canonical,
            canonical.replace("github.com", "git\nhub.com"),
            canonical.replace("/tag/", "\\tag\\"),
        )
        for url in malicious:
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "release page"):
                    self.parse(release(html_url=url))

    def test_installer_url_must_match_repository_tag_and_filename(self):
        canonical = installer()["browser_download_url"]
        malicious = (
            None,
            canonical.replace("https://", "http://"),
            canonical.replace("github.com", "evil.test"),
            canonical.replace("github.com", "github.com.evil.test"),
            canonical.replace("github.com", "github.com@evil.test"),
            canonical.replace("github.com", "user:password@github.com"),
            canonical.replace("github.com", "github.com:443"),
            canonical.replace("github.com", "github.com:8443"),
            canonical.replace("mykolakhy/PixelKit", "mykolakhy/OtherKit"),
            canonical.replace("/v1.10.0/", "/v1.9.0/"),
            canonical.replace("arm64.dmg", "x86_64.dmg"),
            canonical.replace("/download/", "/download/%2e%2e/download/"),
            canonical.replace("PixelKit-", "%50ixelKit-"),
            canonical + "?download=1",
            canonical + "#fragment",
            canonical + "/extra.dmg",
            canonical + "\n",
            canonical.replace("/download/", "\\download\\"),
        )
        for url in malicious:
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "installer download"):
                    self.parse(release(assets=[installer(browser_download_url=url)]))

    def test_unused_asset_urls_do_not_block_a_valid_release(self):
        payload = release(assets=[installer(arch="x86_64", browser_download_url="https://evil.test/download"), installer()])
        self.assertEqual(self.parse(payload).download_url, installer()["browser_download_url"])
        payload["assets"] = [installer(browser_download_url="https://evil.test/download")]
        self.assertIsNone(self.parse(payload, system="Windows", machine="amd64").download_url)

    def test_input_is_not_mutated(self):
        payload = release()
        before = copy.deepcopy(payload)
        self.parse(payload)
        self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main()
