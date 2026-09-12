"""Promotion guards are exercised without contacting or mutating GitHub."""

import argparse
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import promotion
from scripts.release import TARGETS, archive_bundle, digest, make_manifest


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.commit = "a" * 40
        self.version = "0.1.0"
        self.release = {
            "id": 123, "draft": True, "tag_name": "v0.1.0", "target_commitish": self.commit,
        }
        self.assets = [
            {
                "id": index, "name": name, "size": 7, "state": "uploaded",
                "updated_at": "2026-09-12T00:00:00Z", "digest": "sha256:" + "b" * 64,
            }
            for index, name in enumerate(sorted(promotion.expected_names(self.version)))
        ]

    def inspect(self, assets=None, release=None, expected=None):
        with patch.object(promotion, "gh_json", side_effect=[
            self.release if release is None else release,
            self.assets if assets is None else assets,
        ]):
            return promotion.inspect_draft(
                promotion.REPOSITORY, 123, self.version, self.commit, expected,
            )

    def test_private_repository_is_mandatory(self):
        with patch.object(promotion, "gh_json") as api:
            with self.assertRaisesRegex(ValueError, "restricted"):
                promotion.check_repository("other/public")
            api.assert_not_called()
        with patch.object(promotion, "gh_json", return_value={"private": False}):
            with self.assertRaisesRegex(ValueError, "private"):
                promotion.check_repository(promotion.REPOSITORY)

    def test_candidate_requires_unchanged_tag_including_annotated_tags(self):
        with patch.object(promotion, "gh_json", side_effect=[
            {"object": {"type": "tag", "sha": "b" * 40}},
            {"object": {"type": "commit", "sha": self.commit}},
        ]):
            promotion.check_candidate(promotion.REPOSITORY, self.version, self.commit)
        with patch.object(promotion, "gh_json", return_value={
            "object": {"type": "commit", "sha": "c" * 40},
        }):
            with self.assertRaisesRegex(ValueError, "moved"):
                promotion.check_candidate(promotion.REPOSITORY, self.version, self.commit)
        with patch.object(promotion, "gh_json") as api:
            with self.assertRaises(ValueError):
                promotion.check_candidate(promotion.REPOSITORY, "v1;bad", self.commit)
            api.assert_not_called()

    def test_exact_draft_asset_set_required(self):
        self.inspect()
        for assets in ([], self.assets[:-1], [*self.assets, self.assets[0]]):
            with self.assertRaisesRegex(ValueError, "assets"):
                self.inspect(assets=assets)
        assets = copy.deepcopy(self.assets)
        assets[0]["state"] = "new"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.inspect(assets=assets)

    def test_published_release_wrong_tag_or_wrong_commit_is_not_reused(self):
        for field, value in (
            ("draft", False), ("tag_name", "v0.2.0"), ("target_commitish", "main"), ("id", 124),
        ):
            with self.subTest(field=field):
                release = {**self.release, field: value}
                with self.assertRaisesRegex(ValueError, "draft"):
                    self.inspect(release=release)

    def test_replaced_asset_invalidates_candidate_even_when_name_and_size_match(self):
        expected = promotion.asset_fingerprint(self.assets)
        self.inspect(expected=expected)
        for field, value in (("id", 9999), ("digest", "sha256:" + "c" * 64), ("updated_at", "later")):
            assets = copy.deepcopy(self.assets)
            assets[0][field] = value
            with self.assertRaisesRegex(ValueError, "changed"):
                self.inspect(assets=assets, expected=expected)

    def test_fingerprint_ignores_download_count_and_order(self):
        assets = list(reversed(copy.deepcopy(self.assets)))
        assets[0]["download_count"] = 99
        self.assertEqual(promotion.asset_fingerprint(assets), promotion.asset_fingerprint(self.assets))

    def test_download_uses_numeric_asset_id_and_checks_transferred_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset"

            def transfer(command, **kwargs):
                self.assertIn(f"repos/{promotion.REPOSITORY}/releases/assets/17", command)
                self.assertIn("Accept: application/octet-stream", command)
                kwargs["stdout"].write(b"payload")

            with patch.object(promotion.subprocess, "run", side_effect=transfer):
                promotion.download_asset(promotion.REPOSITORY, {"id": 17, "size": 7}, destination)
            self.assertEqual(destination.read_bytes(), b"payload")
            with patch.object(promotion.subprocess, "run", side_effect=transfer):
                with self.assertRaisesRegex(ValueError, "size"):
                    promotion.download_asset(
                        promotion.REPOSITORY, {"id": 17, "size": 99}, Path(temporary) / "bad",
                    )

    def test_download_rejects_substituted_manifest_before_extracting(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(
                repository=promotion.REPOSITORY, release_id=123, version=self.version,
                commit=self.commit, assets_fingerprint=promotion.asset_fingerprint(self.assets),
                target="linux-arm64", destination=Path(temporary) / "download",
                manifest_sha="f" * 64,
            )

            def transfer(repository, asset, destination):
                destination.write_bytes(b"substituted manifest or archive")

            with (
                patch.object(promotion, "check_repository"),
                patch.object(promotion, "check_candidate"),
                patch.object(promotion, "inspect_draft", return_value=(self.release, self.assets)),
                patch.object(promotion, "download_asset", side_effect=transfer),
                patch.object(promotion, "extract_archive") as extract,
            ):
                with self.assertRaisesRegex(ValueError, "manifest"):
                    promotion.download_candidate(args)
                extract.assert_not_called()

    def test_downloaded_candidate_round_trip_is_anchored_to_original_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets_dir = root / "assets"
            for target in TARGETS:
                bundle = root / f"apmx-{target}"
                (bundle / "_internal").mkdir(parents=True)
                (bundle / "LICENSES").mkdir()
                for name in ("LICENSE", "NOTICE", "LICENSES/Python-LICENSE.txt", "LICENSES/manifest.json", "_internal/runtime"):
                    (bundle / name).write_bytes(b"fixture bytes")
                executable = bundle / ("apmx.exe" if target.startswith("windows") else "apmx")
                executable.write_bytes(b"fixture executable bytes")
                executable.chmod(0o755)
                archive_bundle(bundle, assets_dir, self.version, target)
            manifest = make_manifest(assets_dir, self.version, self.commit)
            uploaded = [
                {**asset, "size": (assets_dir / asset["name"]).stat().st_size}
                for asset in self.assets
            ]
            args = argparse.Namespace(
                repository=promotion.REPOSITORY, release_id=123, version=self.version,
                commit=self.commit, assets_fingerprint=promotion.asset_fingerprint(uploaded),
                target="linux-arm64", destination=root / "download", manifest_sha=digest(manifest),
            )

            def transfer(repository, asset, destination):
                shutil.copyfile(assets_dir / asset["name"], destination)

            with (
                patch.object(promotion, "check_repository"),
                patch.object(promotion, "check_candidate"),
                patch.object(promotion, "inspect_draft", return_value=(self.release, uploaded)) as inspect,
                patch.object(promotion, "download_asset", side_effect=transfer) as download,
                patch.object(promotion, "output_values") as output,
            ):
                promotion.download_candidate(args)
            self.assertEqual(download.call_count, 3)
            self.assertEqual(inspect.call_count, 2)
            bundle = Path(output.call_args.kwargs["bundle"])
            self.assertEqual((bundle / "_internal/runtime").read_bytes(), b"fixture bytes")
            copied = json.loads((args.destination / "release-manifest.json").read_bytes())
            self.assertEqual(copied["commit"], self.commit)

    def test_publish_fails_closed_without_mutation_when_asset_identity_changes(self):
        args = argparse.Namespace(
            repository=promotion.REPOSITORY, release_id=123, version=self.version,
            commit=self.commit, assets_fingerprint="old-fingerprint",
        )
        with (
            patch.object(promotion, "check_repository"),
            patch.object(promotion, "check_candidate"),
            patch.object(promotion, "inspect_draft", side_effect=ValueError("changed assets")),
            patch.object(promotion.subprocess, "run") as mutate,
        ):
            with self.assertRaisesRegex(ValueError, "changed"):
                promotion.publish(args)
            mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
