"""A review is bound to the exact repository HEAD and tree it was given.

Reproduces the 0.4.1 acceptance defect: a review recorded at HEAD A stayed
`current` after HEAD moved to B, because the task/evidence/delta fingerprint
does not change when an empty commit leaves the tree and the working delta
untouched. `sera seal` could then bind B on the strength of a review of A.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet, next_action
from sera.core import (
    PACKET_LEGACY_SCHEMA,
    PACKET_STALE_HEAD,
    PACKET_STALE_HEAD_TREE,
    REVIEW_HEAD_MISMATCH,
    REVIEW_HEAD_TREE_MISMATCH,
    REVIEW_REPOSITORY_UNBOUND,
    SEAL_HEAD_MISMATCH,
    SeraError,
    accept_review,
    build_repo_map,
    check_task,
    create_seal,
    evaluate_review_record,
    git_head_identity,
    initialize,
    load_task,
    packet_provenance_path,
    packet_state,
    read_reviews,
    record_review,
    new_task,
    task_fingerprint,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class ReviewIdentityRepository:
    """A committed repository whose SERA state is local, not tracked policy."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
        (self.root / "README.md").write_text("readme\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def reviewable_task(self, mode: str = "standard", risk: str = "medium") -> Path:
        task_dir = new_task(
            self.root, "adjust answer", "adjust the returned number", mode, risk,
            ["src/app.py"], [], [], 1, "implementation",
        )
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        build_packet(self.root, task_dir, "build")
        build_packet(self.root, task_dir, "review")
        return task_dir

    def review_state(self, task_dir: Path) -> dict:
        return packet_state(
            self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
        )


class ReviewPacketIdentityTests(ReviewIdentityRepository, unittest.TestCase):
    def test_review_packet_binds_the_exact_head_and_tree(self) -> None:
        task_dir = self.reviewable_task()
        provenance = json.loads(packet_provenance_path(task_dir, "review").read_text(encoding="utf-8"))
        identity = git_head_identity(self.root)
        self.assertEqual(provenance["repository_identity"], identity)
        self.assertTrue(provenance["coverage_complete"])
        self.assertEqual(len(provenance["review_change_fingerprint"]), 64)
        self.assertTrue(self.review_state(task_dir)["current"])

    def test_review_packet_body_states_the_reviewed_identity(self) -> None:
        task_dir = self.reviewable_task()
        text = (task_dir / "packet-review.md").read_text(encoding="utf-8")
        identity = git_head_identity(self.root)
        self.assertIn("## Repository review identity", text)
        self.assertIn(identity["head_sha"], text)
        self.assertIn(identity["head_tree_sha"], text)
        self.assertIn("Change coverage: complete", text)

    # --- A1: empty commit stales the packet ---------------------------------
    def test_empty_commit_stales_the_review_packet(self) -> None:
        task_dir = self.reviewable_task()
        before = task_fingerprint(self.root, task_dir)
        git(self.root, "commit", "--allow-empty", "-m", "no-op commit")
        # Neither the tree nor the working delta moved: only HEAD binding catches this.
        self.assertEqual(before, task_fingerprint(self.root, task_dir))
        state = self.review_state(task_dir)
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_STALE_HEAD)
        self.assertEqual(next_action(self.root, task_dir)["state"], "review_packet")

    def test_tree_movement_stales_the_review_packet(self) -> None:
        task_dir = self.reviewable_task()
        provenance_path = packet_provenance_path(task_dir, "review")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        # Keep the bound HEAD, move only the bound tree, so the tree branch of
        # the check is exercised on its own.
        provenance["repository_identity"]["head_tree_sha"] = "0" * 40
        provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
        self.assertEqual(self.review_state(task_dir)["reason"], PACKET_STALE_HEAD_TREE)

    def test_unchanged_head_keeps_the_review_packet_current(self) -> None:
        task_dir = self.reviewable_task()
        self.assertTrue(self.review_state(task_dir)["current"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_review")

    def test_041_review_packet_provenance_fails_closed(self) -> None:
        task_dir = self.reviewable_task()
        path = packet_provenance_path(task_dir, "review")
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy["schema_version"] = 2
        legacy.pop("repository_identity")
        legacy.pop("review_change_fingerprint")
        legacy.pop("coverage_complete")
        path.write_text(json.dumps(legacy, indent=2, sort_keys=True), encoding="utf-8")
        state = self.review_state(task_dir)
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_LEGACY_SCHEMA)


class ReviewRecordIdentityTests(ReviewIdentityRepository, unittest.TestCase):
    # --- A2/A3: an empty commit stales an accepted review -------------------
    def test_empty_commit_stales_an_accepted_review(self) -> None:
        task_dir = self.reviewable_task()
        review = accept_review(self.root, task_dir, "ship", "peer", "correct")
        self.assertEqual(review["repository_identity"], git_head_identity(self.root))
        before = check_task(self.root, task_dir)
        self.assertEqual(before["stale_reviews"], [])
        self.assertTrue(before["ok"])

        git(self.root, "commit", "--allow-empty", "-m", "no-op commit")
        after = check_task(self.root, task_dir)
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        self.assertEqual(
            review["repository_identity"]["head_tree_sha"], after["head_identity"]["head_tree_sha"]
        )
        self.assertNotEqual(review["repository_identity"]["head_sha"], after["head_identity"]["head_sha"])
        self.assertEqual(after["stale_reviews"], ["independent"])
        self.assertEqual(after["stale_review_reasons"]["independent"], [REVIEW_HEAD_MISMATCH])
        self.assertFalse(after["ok"])

    # --- A4: a changed tree stales an accepted review -----------------------
    def test_committing_the_change_stales_an_accepted_review(self) -> None:
        task_dir = self.reviewable_task()
        accept_review(self.root, task_dir, "ship", "peer", "correct")
        git(self.root, "add", "src/app.py")
        git(self.root, "commit", "-m", "land the change")
        result = check_task(self.root, task_dir)
        self.assertIn("independent", result["stale_reviews"])
        self.assertIn(REVIEW_HEAD_MISMATCH, result["stale_review_reasons"]["independent"])

    def test_head_tree_mismatch_is_reported_on_its_own(self) -> None:
        identity = {"head_sha": "a" * 40, "head_tree_sha": "b" * 40}
        review = {"fingerprint": "f", "verdict": "ship", "repository_identity": identity}
        same_head = {"head_sha": "a" * 40, "head_tree_sha": "c" * 40}
        state = evaluate_review_record(review, "f", same_head)
        self.assertFalse(state["current"])
        self.assertEqual(state["reasons"], [REVIEW_HEAD_TREE_MISMATCH])

    # --- A5: an untouched HEAD keeps the review current ---------------------
    def test_unchanged_head_keeps_the_review_current(self) -> None:
        task_dir = self.reviewable_task()
        accept_review(self.root, task_dir, "ship", "peer", "correct")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["stale_reviews"], [])
        self.assertEqual(result["review_states"]["independent"]["status"], "current")
        self.assertTrue(result["ok"])

    # --- A6: a 0.4.1 review record cannot satisfy 0.4.2 acceptance ----------
    def test_legacy_unbound_review_record_fails_closed(self) -> None:
        task_dir = self.reviewable_task()
        record_review(task_dir, task_fingerprint(self.root, task_dir), "ship", "peer", "0.4.1 review")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["stale_reviews"], ["independent"])
        self.assertEqual(result["stale_review_reasons"]["independent"], [REVIEW_REPOSITORY_UNBOUND])
        self.assertFalse(result["ok"])
        self.assertIn("exact-HEAD binding", result["next_action"])
        # The record itself remains readable history.
        self.assertEqual(len(read_reviews(task_dir)), 1)

    def test_malformed_review_identity_fails_closed(self) -> None:
        task_dir = self.reviewable_task()
        fingerprint = task_fingerprint(self.root, task_dir)
        path = task_dir / "reviews.jsonl"
        for identity in ({}, {"head_sha": ""}, {"head_tree_sha": "x" * 40}, "not-an-object"):
            path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "fingerprint": fingerprint,
                        "stage": "independent",
                        "verdict": "ship",
                        "reviewer": "peer",
                        "reason": "malformed identity",
                        "repository_identity": identity,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            result = check_task(self.root, task_dir)
            self.assertEqual(
                result["stale_review_reasons"]["independent"], [REVIEW_REPOSITORY_UNBOUND], msg=repr(identity)
            )

    def test_review_is_refused_when_the_packet_is_stale(self) -> None:
        task_dir = self.reviewable_task()
        git(self.root, "commit", "--allow-empty", "-m", "head moves")
        with self.assertRaises(SeraError) as caught:
            accept_review(self.root, task_dir, "ship", "peer", "reviewed the old HEAD")
        self.assertIn(PACKET_STALE_HEAD, str(caught.exception))
        self.assertEqual(read_reviews(task_dir), [])

    def test_review_is_refused_without_any_review_packet(self) -> None:
        task_dir = new_task(
            self.root, "unreviewed", "adjust the returned number", "standard", "medium",
            ["src/app.py"], [], [], 1, "implementation",
        )
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        with self.assertRaises(SeraError):
            accept_review(self.root, task_dir, "ship", "peer", "no packet")

    def test_review_is_refused_when_the_packet_body_was_edited(self) -> None:
        task_dir = self.reviewable_task()
        with (task_dir / "packet-review.md").open("a", encoding="utf-8") as handle:
            handle.write("\nINJECTED VERDICT: ship\n")
        with self.assertRaises(SeraError):
            accept_review(self.root, task_dir, "ship", "peer", "tampered packet")


class ReleaseGateIdentityTests(ReviewIdentityRepository, unittest.TestCase):
    """The release gate is a review stage and binds identity identically."""

    def gated_task(self) -> Path:
        task_dir = new_task(
            self.root, "gated change", "adjust the returned number", "assured", "high",
            ["src/app.py"], [], [], 1, "implementation",
        )
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        build_packet(self.root, task_dir, "build")
        build_packet(self.root, task_dir, "review")
        return task_dir

    # --- B7: a gate cannot inherit an independent review of another HEAD ----
    def test_gate_cannot_rely_on_an_independent_review_of_another_head(self) -> None:
        task_dir = self.gated_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        git(self.root, "commit", "--allow-empty", "-m", "head moves")

        result = check_task(self.root, task_dir)
        self.assertEqual(result["stale_reviews"], ["independent"])
        self.assertEqual(result["missing_reviews"], ["gate"])
        self.assertFalse(result["ok"])
        # The stale earlier stage outranks the missing later one.
        self.assertEqual(next_action(self.root, task_dir)["state"], "review")

        with self.assertRaises(SeraError):
            accept_review(self.root, task_dir, "ship", "release-gate", "gate on a moved HEAD", "gate")

    # --- B8: both stages at the same HEAD seal cleanly ----------------------
    def test_both_stages_at_the_same_head_seal_successfully(self) -> None:
        task_dir = self.gated_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        accept_review(self.root, task_dir, "ship", "release-gate", "acceptable", "gate")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["ok"], msg=result["next_action"])
        seal = create_seal(self.root, task_dir)
        self.assertEqual(seal["repository_identity"], git_head_identity(self.root))
        self.assertEqual(next_action(self.root, task_dir)["state"], "accepted")

    # --- B9: an empty commit after review refuses the seal ------------------
    def test_seal_is_refused_after_head_moves(self) -> None:
        task_dir = self.gated_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        accept_review(self.root, task_dir, "ship", "release-gate", "acceptable", "gate")
        self.assertTrue(check_task(self.root, task_dir)["ok"])

        git(self.root, "commit", "--allow-empty", "-m", "head moves")
        with self.assertRaises(SeraError) as caught:
            create_seal(self.root, task_dir)
        self.assertIn("HEAD moved after review", str(caught.exception))
        self.assertFalse((task_dir / "seal.json").exists())

    # --- C10: post-seal identity checking is unchanged ----------------------
    def test_seal_created_at_a_reviewed_head_goes_stale_when_head_moves(self) -> None:
        task_dir = self.gated_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        accept_review(self.root, task_dir, "ship", "release-gate", "acceptable", "gate")
        create_seal(self.root, task_dir)
        self.assertFalse(check_task(self.root, task_dir)["seal_stale"])

        git(self.root, "commit", "--allow-empty", "-m", "head moves")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_HEAD_MISMATCH, result["seal_stale_reasons"])


if __name__ == "__main__":
    unittest.main()
