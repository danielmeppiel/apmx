"""Promotion guards are exercised without contacting or mutating GitHub."""

import argparse
import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import promotion
from scripts.release import TARGETS, archive_bundle, digest, make_manifest
from tests.release.test_backend import add_backend_fixture


class PromotionTests(unittest.TestCase):
    def test_release_notes_bind_candidate_and_preserve_migration_and_assurance_limits(self):
        notes = promotion.release_notes("0.3.0", "a" * 40)
        for text in (
            "Standalone apmx 0.3.0",
            "`" + "a" * 40 + "`",
            "multiple output files",
            "original checkout",
            "optional Gherkin/Behave",
            "BREAKING: imported skill metadata",
            "nonempty `name` and `description`",
            "authored source package",
            "Do not patch generated `apm_modules`",
            "apm-contract-run/0.1",
            "apm-contract-run/0.2",
            "--allow-host-access --allow-unproven-inputs",
            "UNPROVEN (21)",
            "NOT live inference",
            "APM 0.30.0",
            "not publisher authentication",
            "No trusted publisher signature or Apple notarization",
        ):
            with self.subTest(text=text):
                self.assertIn(text, notes)

    def setUp(self):
        self.commit = "a" * 40
        self.version = "0.1.0"
        self.release = {
            "id": 123,
            "draft": True,
            "tag_name": "v0.1.0",
            "target_commitish": self.commit,
        }
        self.assets = [
            {
                "id": index,
                "name": name,
                "size": 7,
                "state": "uploaded",
                "updated_at": "2026-09-12T00:00:00Z",
                "digest": "sha256:" + "b" * 64,
            }
            for index, name in enumerate(sorted(promotion.expected_names(self.version)))
        ]

    def inspect(self, assets=None, release=None, expected=None):
        with patch.object(
            promotion,
            "gh_json",
            side_effect=[
                self.release if release is None else release,
                self.assets if assets is None else assets,
            ],
        ):
            return promotion.inspect_draft(
                promotion.REPOSITORY,
                123,
                self.version,
                self.commit,
                expected,
            )

    def test_private_repository_is_mandatory(self):
        with patch.object(promotion, "gh_json") as api:
            with self.assertRaisesRegex(ValueError, "restricted"):
                promotion.check_repository("other/public")
            api.assert_not_called()
        with (
            patch.object(promotion, "gh_json", return_value={"private": False}),
            self.assertRaisesRegex(ValueError, "private"),
        ):
            promotion.check_repository(promotion.REPOSITORY)

    def test_public_promotion_requires_explicit_standalone_nonfork_main_policy(self):
        metadata = {
            "private": False,
            "full_name": promotion.REPOSITORY,
            "fork": False,
            "default_branch": "main",
        }
        with patch.object(promotion, "gh_json", return_value=metadata):
            promotion.check_repository(promotion.REPOSITORY, public_release=True)
        for field, value in (
            ("private", True),
            ("full_name", "other/repository"),
            ("fork", True),
            ("default_branch", "unexpected"),
        ):
            with (
                self.subTest(field=field),
                patch.object(promotion, "gh_json", return_value={**metadata, field: value}),
                self.assertRaisesRegex(ValueError, "public"),
            ):
                promotion.check_repository(promotion.REPOSITORY, public_release=True)

    def test_public_draft_must_remain_an_experimental_prerelease(self):
        with (
            patch.object(
                promotion,
                "gh_json",
                side_effect=[self.release, self.assets],
            ),
            self.assertRaisesRegex(ValueError, "prerelease"),
        ):
            promotion.inspect_draft(
                promotion.REPOSITORY,
                123,
                self.version,
                self.commit,
                public_release=True,
            )

    def test_candidate_requires_unchanged_tag_including_annotated_tags(self):
        with patch.object(
            promotion,
            "gh_json",
            side_effect=[
                {"object": {"type": "tag", "sha": "b" * 40}},
                {"object": {"type": "commit", "sha": self.commit}},
            ],
        ):
            promotion.check_candidate(promotion.REPOSITORY, self.version, self.commit)
        with (
            patch.object(
                promotion,
                "gh_json",
                return_value={
                    "object": {"type": "commit", "sha": "c" * 40},
                },
            ),
            self.assertRaisesRegex(ValueError, "moved"),
        ):
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
            ("draft", False),
            ("tag_name", "v0.2.0"),
            ("target_commitish", "main"),
            ("id", 124),
        ):
            with self.subTest(field=field):
                release = {**self.release, field: value}
                with self.assertRaisesRegex(ValueError, "draft"):
                    self.inspect(release=release)

    def test_replaced_asset_invalidates_candidate_even_when_name_and_size_match(self):
        expected = promotion.asset_fingerprint(self.assets)
        self.inspect(expected=expected)
        for field, value in (
            ("id", 9999),
            ("digest", "sha256:" + "c" * 64),
            ("updated_at", "later"),
        ):
            assets = copy.deepcopy(self.assets)
            assets[0][field] = value
            with self.assertRaisesRegex(ValueError, "changed"):
                self.inspect(assets=assets, expected=expected)

    def test_fingerprint_ignores_download_count_and_order(self):
        assets = list(reversed(copy.deepcopy(self.assets)))
        assets[0]["download_count"] = 99
        self.assertEqual(
            promotion.asset_fingerprint(assets), promotion.asset_fingerprint(self.assets)
        )

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
            with (
                patch.object(promotion.subprocess, "run", side_effect=transfer),
                self.assertRaisesRegex(ValueError, "size"),
            ):
                promotion.download_asset(
                    promotion.REPOSITORY,
                    {"id": 17, "size": 99},
                    Path(temporary) / "bad",
                )

    def test_download_rejects_substituted_manifest_before_extracting(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(
                public_release=False,
                repository=promotion.REPOSITORY,
                release_id=123,
                version=self.version,
                commit=self.commit,
                assets_fingerprint=promotion.asset_fingerprint(self.assets),
                target="linux-arm64",
                destination=Path(temporary) / "download",
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
                for name in (
                    "LICENSE",
                    "NOTICE",
                    "LICENSES/Python-LICENSE.txt",
                    "LICENSES/manifest.json",
                    "_internal/runtime",
                ):
                    (bundle / name).write_bytes(b"fixture bytes")
                executable = bundle / ("apmx.exe" if target.startswith("windows") else "apmx")
                executable.write_bytes(b"fixture executable bytes")
                executable.chmod(0o755)
                add_backend_fixture(bundle, target)
                archive_bundle(bundle, assets_dir, self.version, target)
            manifest = make_manifest(assets_dir, self.version, self.commit)
            uploaded = [
                {**asset, "size": (assets_dir / asset["name"]).stat().st_size}
                for asset in self.assets
            ]
            args = argparse.Namespace(
                public_release=False,
                repository=promotion.REPOSITORY,
                release_id=123,
                version=self.version,
                commit=self.commit,
                assets_fingerprint=promotion.asset_fingerprint(uploaded),
                target="linux-arm64",
                destination=root / "download",
                manifest_sha=digest(manifest),
            )

            def transfer(repository, asset, destination):
                shutil.copyfile(assets_dir / asset["name"], destination)

            with (
                patch.object(promotion, "check_repository"),
                patch.object(promotion, "check_candidate"),
                patch.object(
                    promotion, "inspect_draft", return_value=(self.release, uploaded)
                ) as inspect,
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
            public_release=False,
            repository=promotion.REPOSITORY,
            release_id=123,
            version=self.version,
            commit=self.commit,
            assets_fingerprint="old-fingerprint",
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

    def test_public_draft_preflight_rejects_wrong_inner_source_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            for metadata in (
                {"version": self.version},
                {"version": self.version, "source_commit": "d" * 40},
                {"version": "9.9.9", "source_commit": self.commit},
            ):
                (bundle / "RELEASE.json").write_text(json.dumps(metadata), encoding="utf-8")
                with (
                    self.subTest(metadata=metadata),
                    patch.object(promotion, "check_repository"),
                    patch.object(promotion, "check_candidate"),
                    patch.object(promotion, "make_manifest"),
                    patch.object(promotion, "extract_archive", return_value=bundle),
                    patch.object(promotion.subprocess, "run") as upload,
                    self.assertRaisesRegex(ValueError, "reviewed source candidate"),
                ):
                    promotion.create_draft(
                        promotion.REPOSITORY,
                        root,
                        self.version,
                        self.commit,
                        public_release=True,
                    )
                upload.assert_not_called()

    def verification_fixture(self, receipt_changes=None):
        args = argparse.Namespace(
            repository=promotion.REPOSITORY,
            version=self.version,
            commit=self.commit,
            release_id=123,
            assets_fingerprint=promotion.asset_fingerprint(self.assets),
            public_release=True,
            verified_run_id=456,
        )
        run = {
            "id": 456,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "path": promotion.PUBLIC_WORKFLOW,
            "head_sha": self.commit,
            "head_branch": "main",
            "repository": {"full_name": promotion.REPOSITORY},
            "head_repository": {"full_name": promotion.REPOSITORY},
            "run_attempt": 1,
        }
        jobs = {
            "total_count": 5,
            "jobs": [
                {
                    "id": 1000 + index,
                    "run_id": 456,
                    "run_attempt": 1,
                    "head_sha": self.commit,
                    "name": f"Downloaded {target}",
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, target in enumerate(TARGETS)
            ],
        }
        receipt = {
            "schema": promotion.VERIFICATION_SCHEMA,
            "repository": promotion.REPOSITORY,
            "version": self.version,
            "commit": self.commit,
            "release_id": 123,
            "manifest_sha": "c" * 64,
            "assets_fingerprint": args.assets_fingerprint,
            "workflow": promotion.PUBLIC_WORKFLOW,
            "run_id": 456,
            "run_attempt": 1,
            **(receipt_changes or {}),
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(promotion.VERIFICATION_FILE, json.dumps(receipt))
        payload = stream.getvalue()
        artifacts = {
            "total_count": 1,
            "artifacts": [
                {
                    "id": 789,
                    "name": promotion.VERIFICATION_ARTIFACT,
                    "expired": False,
                    "size_in_bytes": len(payload),
                    "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "workflow_run": {"id": 456, "head_sha": self.commit},
                }
            ],
        }
        return args, [run, [jobs], artifacts], payload

    def test_receipt_only_partial_retry_reuses_latest_successful_verifier_execution(self):
        args, responses, payload = self.verification_fixture({"run_attempt": 2})
        responses[0]["run_attempt"] = 2
        receipt_job = {
            "id": 2000,
            "run_id": 456,
            "run_attempt": 2,
            "head_sha": self.commit,
            "name": "verified",
            "status": "completed",
            "conclusion": "success",
        }

        def api(*arguments):
            endpoint = arguments[-1]
            if endpoint.endswith("/actions/runs/456"):
                return responses[0]
            if "filter=all" in endpoint:
                jobs = [*responses[1][0]["jobs"], receipt_job]
                return [
                    {"total_count": 6, "jobs": jobs[:3]},
                    {"total_count": 6, "jobs": jobs[3:]},
                ]
            if "/attempts/2/jobs?" in endpoint:
                return {"total_count": 1, "jobs": [receipt_job]}
            if "/artifacts?" in endpoint:
                return responses[2]
            self.fail(f"Unexpected preparation API request: {endpoint}")

        with (
            patch.object(promotion, "gh_json", side_effect=api) as requests,
            patch.object(promotion.subprocess, "check_output", return_value=payload),
        ):
            receipt = promotion.check_verified_receipt(args)
        self.assertEqual(receipt["run_attempt"], 2)
        jobs_request = next(
            call.args for call in requests.call_args_list if "filter=all" in call.args[-1]
        )
        self.assertIn("--paginate", jobs_request)
        self.assertIn("--slurp", jobs_request)

    def test_latest_failed_skipped_or_incomplete_verifier_never_falls_back_to_success(self):
        for status, conclusion in (
            ("completed", "failure"),
            ("completed", "skipped"),
            ("in_progress", None),
            ("queued", "success"),
        ):
            args, responses, _ = self.verification_fixture({"run_attempt": 2})
            responses[0]["run_attempt"] = 2
            page = responses[1][0]
            page["jobs"].insert(
                0,
                {
                    **page["jobs"][0],
                    "id": 2000,
                    "run_attempt": 2,
                    "status": status,
                    "conclusion": conclusion,
                },
            )
            page["total_count"] += 1
            with (
                self.subTest(status=status, conclusion=conclusion),
                patch.object(promotion, "gh_json", side_effect=responses),
                patch.object(promotion.subprocess, "check_output") as download,
                self.assertRaisesRegex(ValueError, "downloaded-byte verification"),
            ):
                promotion.check_verified_receipt(args)
            download.assert_not_called()

    def test_latest_successful_verifier_retry_replaces_its_prior_failure(self):
        args, responses, payload = self.verification_fixture({"run_attempt": 2})
        responses[0]["run_attempt"] = 2
        page = responses[1][0]
        retry = {**page["jobs"][0], "id": 2000, "run_attempt": 2}
        page["jobs"][0]["conclusion"] = "failure"
        page["jobs"].insert(0, retry)
        page["total_count"] += 1
        with (
            patch.object(promotion, "gh_json", side_effect=responses),
            patch.object(promotion.subprocess, "check_output", return_value=payload),
        ):
            self.assertEqual(promotion.check_verified_receipt(args)["run_attempt"], 2)

    def test_stale_receipt_after_partial_retry_never_authorizes_publication(self):
        args, responses, payload = self.verification_fixture()
        responses[0]["run_attempt"] = 2
        with (
            patch.object(promotion, "gh_json", side_effect=responses),
            patch.object(promotion.subprocess, "check_output", return_value=payload),
            self.assertRaisesRegex(ValueError, "different candidate or attempt"),
        ):
            promotion.check_verified_receipt(args)

    def test_invalid_cross_run_future_or_duplicate_latest_verifier_evidence_refuses(self):
        for changes in (
            {"id": 0},
            {"run_id": 999},
            {"head_sha": "d" * 40},
            {"run_attempt": True},
            {"run_attempt": 3},
            {"duplicate": True},
            {"incomplete_pages": True},
        ):
            args, responses, _ = self.verification_fixture({"run_attempt": 2})
            responses[0]["run_attempt"] = 2
            page = responses[1][0]
            if changes.get("duplicate"):
                page["jobs"].append({**page["jobs"][0], "id": 2000})
                page["total_count"] += 1
            elif changes.get("incomplete_pages"):
                page["total_count"] += 1
            else:
                page["jobs"][0].update(changes)
            with (
                self.subTest(changes=changes),
                patch.object(promotion, "gh_json", side_effect=responses),
                patch.object(promotion.subprocess, "check_output") as download,
                self.assertRaisesRegex(ValueError, "verification|Incomplete preparation"),
            ):
                promotion.check_verified_receipt(args)
            download.assert_not_called()

    def test_verified_receipt_requires_exact_successful_new_workflow_and_source(self):
        args, responses, payload = self.verification_fixture()
        with (
            patch.object(promotion, "gh_json", side_effect=responses),
            patch.object(promotion.subprocess, "check_output", return_value=payload),
        ):
            receipt = promotion.check_verified_receipt(args)
        self.assertEqual(receipt["release_id"], 123)
        for field, value in (
            ("status", "in_progress"),
            ("conclusion", "failure"),
            ("event", "push"),
            ("path", ".github/workflows/release.yml"),
            ("head_sha", "d" * 40),
            ("head_branch", "unreviewed"),
            ("head_repository", {"full_name": "fork/apmx"}),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(responses)
                changed[0][field] = value
                with (
                    patch.object(promotion, "gh_json", side_effect=changed),
                    patch.object(promotion.subprocess, "check_output") as download,
                    self.assertRaisesRegex(ValueError, "trusted source workflow"),
                ):
                    promotion.check_verified_receipt(args)
                download.assert_not_called()

    def test_missing_or_skipped_downloaded_target_never_authorizes_publication(self):
        for missing in (True, False):
            args, responses, _ = self.verification_fixture()
            if missing:
                responses[1][0]["jobs"].pop()
                responses[1][0]["total_count"] -= 1
            else:
                responses[1][0]["jobs"][0]["conclusion"] = "skipped"
            with (
                self.subTest(missing=missing),
                patch.object(promotion, "gh_json", side_effect=responses),
                patch.object(promotion.subprocess, "check_output") as download,
                self.assertRaisesRegex(ValueError, "downloaded-byte verification"),
            ):
                promotion.check_verified_receipt(args)
            download.assert_not_called()

    def test_receipt_substitution_expiry_and_transfer_tampering_fail_closed(self):
        for changes in (
            {"release_id": 124},
            {"assets_fingerprint": "d" * 64},
            {"commit": "d" * 40},
            {"run_attempt": 2},
            {"manifest_sha": "not-a-hash"},
            {"manifest_sha": None},
        ):
            args, responses, payload = self.verification_fixture(changes)
            with (
                self.subTest(changes=changes),
                patch.object(promotion, "gh_json", side_effect=responses),
                patch.object(promotion.subprocess, "check_output", return_value=payload),
                self.assertRaisesRegex(ValueError, "receipt|manifest anchor"),
            ):
                promotion.check_verified_receipt(args)
        args, responses, payload = self.verification_fixture()
        responses[2]["artifacts"][0]["expired"] = True
        with (
            patch.object(promotion, "gh_json", side_effect=responses),
            self.assertRaisesRegex(ValueError, "expired"),
        ):
            promotion.check_verified_receipt(args)
        args, responses, payload = self.verification_fixture()
        with (
            patch.object(promotion, "gh_json", side_effect=responses),
            patch.object(promotion.subprocess, "check_output", return_value=payload[:-1] + b"x"),
            self.assertRaisesRegex(ValueError, "transfer/hash"),
        ):
            promotion.check_verified_receipt(args)

    def test_public_publish_requires_receipt_before_any_release_mutation(self):
        args, _, _ = self.verification_fixture()
        with (
            patch.object(promotion, "check_repository"),
            patch.object(promotion, "check_candidate"),
            patch.object(
                promotion, "check_verified_receipt", side_effect=ValueError("unverified run")
            ),
            patch.object(promotion, "inspect_draft") as inspect,
            patch.object(promotion.subprocess, "run") as mutate,
            self.assertRaisesRegex(ValueError, "unverified run"),
        ):
            promotion.publish(args)
        inspect.assert_not_called()
        mutate.assert_not_called()

    def test_public_publish_uses_verified_draft_only_and_preserves_prerelease(self):
        args, responses, payload = self.verification_fixture()
        repository = {
            "private": False,
            "full_name": promotion.REPOSITORY,
            "fork": False,
            "default_branch": "main",
        }
        responses = [
            repository,
            {"object": {"type": "commit", "sha": self.commit}},
            *responses,
            {**self.release, "prerelease": True},
            self.assets,
            {"draft": False, "prerelease": True, "html_url": "https://example.test/release"},
        ]
        with (
            patch.object(promotion, "gh_json", side_effect=responses),
            patch.object(promotion.subprocess, "check_output", return_value=payload),
            patch.object(promotion.subprocess, "run") as mutate,
            patch.object(promotion, "output_values"),
        ):
            promotion.publish(args)
        mutate.assert_called_once()
        command = mutate.call_args.args[0]
        self.assertIn(f"repos/{promotion.REPOSITORY}/releases/123", command)
        self.assertIn("draft=false", command)
        self.assertIn("make_latest=false", command)
        self.assertNotIn("create", command)

    def test_receipt_creation_is_bound_to_trusted_workflow_context_and_never_overwrites(self):
        args, _, _ = self.verification_fixture()
        args.manifest_sha = "c" * 64
        with tempfile.TemporaryDirectory() as temporary:
            args.receipt = Path(temporary) / promotion.VERIFICATION_FILE
            environment = {
                "GITHUB_REPOSITORY": promotion.REPOSITORY,
                "GITHUB_SHA": self.commit,
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_WORKFLOW_REF": (
                    f"{promotion.REPOSITORY}/{promotion.PUBLIC_WORKFLOW}@refs/heads/main"
                ),
                "GITHUB_RUN_ID": "456",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(promotion, "output_values"),
            ):
                promotion.write_verification(args)
                self.assertEqual(json.loads(args.receipt.read_text())["commit"], self.commit)
                with self.assertRaises(FileExistsError):
                    promotion.write_verification(args)
                with (
                    patch.dict(os.environ, {"GITHUB_SHA": "d" * 40}),
                    self.assertRaisesRegex(ValueError, "trusted public preparation"),
                ):
                    promotion.write_verification(args)

    def test_public_archive_metadata_must_match_reviewed_version_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            metadata = bundle / "RELEASE.json"
            metadata.write_text(json.dumps({"version": self.version, "source_commit": self.commit}))
            promotion.check_public_bundle(bundle, self.version, self.commit)
            for change in ({"source_commit": "d" * 40}, {"version": "99.0.0"}):
                metadata.write_text(
                    json.dumps({"version": self.version, "source_commit": self.commit, **change})
                )
                with self.assertRaisesRegex(ValueError, "reviewed source candidate"):
                    promotion.check_public_bundle(bundle, self.version, self.commit)


if __name__ == "__main__":
    unittest.main()
