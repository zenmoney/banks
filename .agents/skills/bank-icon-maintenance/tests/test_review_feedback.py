"""Reviewer intake, queue and revision history over real disposable Git objects.

GitHub text/permissions are controlled API fixtures, not claims of a live agent or
real bank visual review. No production repo, service state or human MR is written.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import asset_publication as assets
import feedback_intake as intake
import feedback_revision as revision
import feedback_publication as publication
import github_pr
import session as sessions
import state_publication as state
import state_results
import source_evidence as se

TARGET = "fixture/catalog"
PATH = "banks/Example_10-ru/icon.svg"
HEAD = "bank-icon-maintenance/example-10"
URL = "https://github.com/user-attachments/assets/5a6f146d-504b-431e-84a9-9639a26c339c"
BODY = f'Обновить на этот\n<img width="54" height="54" alt="BPER Banca, 15908" src="{URL}" />\n'
OLD = "2020-01-01T00:00:00Z"
A = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
B = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="12"/></svg>'
C = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="14"/></svg>'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True, timeout=30).stdout.strip()


class FeedbackFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-feedback-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"; self.repo.mkdir()
        git(self.repo, "init", "-qb", "main")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.put("bank-icon-maintenance.json", state.encoded({"schemaVersion": 1,
            "recheckAfterDays": 90, "retryUnconfirmedAfterDays": 30, "retryErrorAfterDays": 7, "selectionLimit": 1}))
        for id in (10, 20):
            folder = f"banks/Example_{id}-ru"
            self.put(folder + "/info.json", state.encoded({"id": id, "title": "Example", "countryCode": "RU"}))
            self.put(folder + "/icon.svg", A)
        self.base = self.commit()
        self.asset = assets._commit(self.repo, self.root, [(PATH, B)], "original human proposal", self.base)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "-q", str(self.remote))
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-q", "origin", f"{self.base}:refs/heads/main", f"{self.asset}:refs/heads/{HEAD}", f"{self.asset}:refs/pull/146/head")
        self.body = BODY
        self.author = {"__typename": "User", "login": "author"}
        self.editor = {"__typename": "User", "login": "reviewer"}
        self.permission = "write"
        self.comments = []; self.reviews = []; self.inline = []
        self.nodes = {}; self.comment_calls = 0
        self.files = [{"filename": PATH, "status": "modified"}]
        self.api_patch = patch.object(github_pr, "_api", side_effect=self.api).start()
        patch.object(assets, "_remote", return_value=str(self.remote)).start()
        self.addCleanup(patch.stopall)
        self.bank = intake._bank(self.repo, self.base, PATH)

    def put(self, path, content):
        file = self.repo / path; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(content)

    def commit(self):
        git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "fixture")
        return git(self.repo, "rev-parse", "HEAD")

    def ref(self, ref):
        return git(self.remote, "rev-parse", ref)

    def raw_pr(self):
        return {"node_id": "PR_146", "number": 146, "html_url": f"https://github.com/{TARGET}/pull/146",
                "state": "open", "merged_at": None, "title": "Example bank icon", "body": self.body,
                "head": {"ref": HEAD, "sha": self.ref(f"refs/heads/{HEAD}"), "repo": {"full_name": TARGET}},
                "base": {"ref": "main", "sha": self.ref("refs/heads/main"), "repo": {"full_name": TARGET}}}

    def node(self):
        return {"__typename": "PullRequest", "id": "PR_146", "url": f"https://github.com/{TARGET}/pull/146",
                "body": self.body, "createdAt": OLD, "lastEditedAt": "2020-01-02T00:00:00Z",
                "author": self.author, "editor": self.editor}

    def api(self, endpoint, *args):
        def pages(rows):
            if "--slurp" in args:
                return [copy.deepcopy(rows)]
            page = next((int(a.split("=", 1)[1]) for a in args if a.startswith("page=")), 1)
            return copy.deepcopy(rows[(page-1)*100:page*100])
        if endpoint == "graphql":
            fields = dict(arg.split("=", 1) for arg in args if "=" in arg)
            if "id" in fields:
                node = self.node() if fields["id"] == "PR_146" else self.nodes.get(fields["id"])
                return {"data": {"node": copy.deepcopy(node)}}
            return {"data": {"repository": {"nameWithOwner": TARGET, "url": f"https://github.com/{TARGET}",
                    "isPrivate": True, "defaultBranchRef": {"name": "main"}, "viewerPermission": "ADMIN"}}}
        if endpoint.startswith(f"repos/{TARGET}/collaborators/"):
            login = endpoint.split("/")[-2]
            return {"permission": self.permission, "user": {"login": login, "type": "User"}}
        if endpoint in {f"repos/{TARGET}/pulls?state=open", f"repos/{TARGET}/pulls"}:
            return pages([self.raw_pr()])
        if endpoint == f"repos/{TARGET}/pulls/146":
            return self.raw_pr()
        if endpoint == f"repos/{TARGET}/pulls/146/files":
            return pages(self.files)
        if endpoint == f"repos/{TARGET}/issues/146/comments":
            if "POST" in args:
                self.comment_calls += 1
                body = next(a[5:] for a in args if a.startswith("body="))
                row = {"id": 500 + self.comment_calls, "node_id": f"C{self.comment_calls}", "body": body,
                       "html_url": f"https://github.com/{TARGET}/pull/146#issuecomment-{500 + self.comment_calls}"}
                self.comments.append(row)
                return row
            return pages(self.comments)
        if endpoint == f"repos/{TARGET}/pulls/146/reviews":
            return pages(self.reviews)
        if endpoint == f"repos/{TARGET}/pulls/146/comments":
            return pages(self.inline)
        raise AssertionError(f"unexpected GitHub operation: {endpoint} {args}")

    def event(self):
        result = intake.discover(self.repo, self.base, TARGET, None)
        self.assertEqual(result["stops"], [])
        self.assertEqual(len(result["events"]), 1)
        return result["events"][0]

    def decision(self, event, choice="use", supersedes=None):
        return {"key": event["key"], "decision": choice,
                "selectedUrl": URL if choice == "use" else None,
                "quote": "Обновить на этот", "reason": "Explicit selected variant, not a before/after illustration",
                "supersedes": supersedes or []}

    def prior(self):
        mr = {**github_pr.inspect(TARGET, 146), "observedAt": OLD}
        return {"schemaVersion": 1, "lastVerifiedAt": OLD, "verifiedIconHash": state.digest(A),
                "lastAttemptAt": OLD, "lastAttemptResult": "candidate", "customHistory": {"keep": True},
                "proposals": [{"status": "open", "baseIconHash": state.digest(A),
                    "candidateIconHash": state.digest(B), "checkedAt": OLD, "mr": mr,
                    "assetPublication": {"path": "old/missing-local-file.json", "sha256": "a" * 64}}]}

    def queue(self, before=None):
        event = self.event()
        return intake.decision_state(before, event, self.decision(event), intake.now())

    def write_state(self, value):
        commit = assets._commit(self.repo, self.root, [("banks/state/10.json", state.encoded(value))], "durable fixture state")
        git(self.repo, "push", "-q", "origin", f"{commit}:{state.REF}")
        return commit

    def start(self, value, explicit=False):
        self.write_state(value)
        session = self.root / "session"
        with patch.object(intake, "repository_from_remote", return_value=TARGET):
            data = sessions.create_session(self.repo, session, 10, base="main")
            revision.prepare_context(session, data, data["banks"][0])
            sessions.save(session, data)
        return session, data


class GitHubFixture(FeedbackFixture):
    def test_api_pagination_stops_at_bound_without_claiming_a_partial_queue(self):
        calls = []
        def huge(endpoint, *args):
            calls.append(args)
            return [{"number": i} for i in range(100)]
        with patch.object(github_pr, "_api", side_effect=huge):
            with self.assertRaisesRegex(ValueError, "bounded"):
                intake._pages("repos/fixture/catalog/pulls", 100)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("--paginate" not in args for args in calls))

    def test_pr146_html_body_with_no_comments_is_discovered(self):
        event = self.event()
        self.assertEqual(event["source"]["kind"], "body")
        self.assertEqual(event["source"]["attachments"], [URL])
        self.assertEqual(event["bank"]["id"], 10)  # title/alt is not Company.id authority
        self.assertNotIn("width", event["source"])
        self.assertEqual(event["source"]["trust"]["login"], "reviewer")

    def test_permission_is_checked_for_editor_not_original_author(self):
        self.permission = "read"
        result = intake.discover(self.repo, self.base, TARGET, None)
        self.assertEqual(result["events"], [])
        self.assertIn("access", result["stops"][0]["reason"])
        self.editor = None
        result = intake.discover(self.repo, self.base, TARGET, None)
        self.assertEqual(result["events"], [])
        self.assertIn("identified human", result["stops"][0]["reason"])

    def test_missing_editor_and_bot_do_not_fall_back_to_author(self):
        for editor in (None, {"__typename": "Bot", "login": "maintainer-bot"}):
            with self.subTest(editor=editor):
                self.editor = editor
                with self.assertRaises(ValueError):
                    intake.message(TARGET, 146, "body", "PR_146")

    def test_comments_reviews_and_inline_nodes_are_collected(self):
        for kind, typename, fragment, collection in (
                ("comment", "IssueComment", "issuecomment-1", self.comments),
                ("review", "PullRequestReview", "pullrequestreview-2", self.reviews),
                ("inline", "PullRequestReviewComment", "discussion_r3", self.inline)):
            node = {**self.node(), "__typename": typename, "id": kind,
                    "url": f"https://github.com/{TARGET}/pull/146#{fragment}"}
            self.nodes[kind] = node
            collection.append({"node_id": kind, "body": BODY})
        result = intake.discover(self.repo, self.base, TARGET, None)
        self.assertEqual(result["stops"], [])
        self.assertEqual({row["source"]["kind"] for row in result["events"]}, {"body", "comment", "review", "inline"})

    def test_code_examples_are_not_image_requests(self):
        self.assertEqual(intake.attachment_urls(f'```html\n<img src="{URL}"/>\n```\n`![x]({URL})`'), [])

    def test_markdown_reference_html_and_file_links_deduplicate(self):
        self.assertEqual(intake.attachment_urls(f'![one]({URL}) <img src="{URL}"> ![other][logo]\n[logo]: {URL}'), [URL])
        self.assertEqual(intake.attachment_urls('[vector](https://bank.example/brand.svg)'), ['https://bank.example/brand.svg'])
        for url in ("http://example.test/a.png", "https://user:secret@example.test/a.png", "https://example.test/a.png#x"):
            with self.assertRaises(ValueError):
                intake.attachment_urls(f'<img src="{url}">')

    def test_multi_file_and_mismatched_metadata_are_stopped(self):
        self.files.append({"filename": "README.md", "status": "modified"})
        result = intake.discover(self.repo, self.base, TARGET, None)
        self.assertEqual(result["events"], [])
        self.assertIn("single", result["stops"][0]["reason"])

    def test_intake_preserves_dates_history_and_does_not_enqueue_twice(self):
        prior = self.prior(); original = copy.deepcopy(prior)
        queued = self.queue(prior)
        self.assertEqual(prior, original)
        self.assertEqual({k: v for k, v in queued.items() if k != "reviewFeedback"}, prior)
        event = queued["reviewFeedback"][0]["event"]
        with self.assertRaisesRegex(ValueError, "already recorded"):
            intake.decision_state(queued, event, self.decision(event), intake.now())
        commit = self.write_state(queued)
        self.assertEqual(intake.discover(self.repo, self.base, TARGET, commit)["events"], [])

    def test_new_body_is_new_event_own_review_section_is_not(self):
        original = self.event()
        self.body += '\nClarified presentation.'
        changed = self.event()
        self.assertNotEqual(changed["key"], original["key"])
        self.body = BODY + publication.review_comment({"requestKey": original["key"], "request": {"event": original}}, '![generated](https://example.test/generated.png)')
        self.assertEqual(self.event()["key"], original["key"])

    def test_source_mutations_and_new_discussion_block_stale_decisions(self):
        event = self.event()
        intake.verify_event(event)
        self.comments.append({"node_id": "new", "body": "Stop, wrong bank"})
        with self.assertRaisesRegex(ValueError, "discussion changed"):
            intake.verify_event(event)

    def test_forged_quote_url_or_source_hash_cannot_enter_queue(self):
        event = self.event()
        for field, value in (("quote", "I approve publication"), ("selectedUrl", "https://other.test/fake.svg")):
            decision = self.decision(event); decision[field] = value
            with self.assertRaises(ValueError):
                intake.decision_state(None, event, decision, intake.now())
        bad = copy.deepcopy(event); bad["source"]["text"] = "invented"
        with self.assertRaises(ValueError):
            intake.decision_state(None, bad, self.decision(bad), intake.now())

    def test_conflicting_requests_need_explicit_supersession(self):
        before = self.queue(self.prior())
        first = before["reviewFeedback"][0]["key"]
        self.body += '\nUse this exact selected version.'
        event = self.event()
        both = intake.decision_state(before, event, self.decision(event), intake.now())
        with self.assertRaisesRegex(ValueError, "conflicting"):
            revision.queued(both)
        resolved = intake.decision_state(before, event, self.decision(event, supersedes=[first]), intake.now())
        self.assertEqual(revision.queued(resolved)["key"], event["key"])
        self.assertEqual(resolved["reviewFeedback"][0]["status"], "superseded")

    def test_next_automatic_queue_prioritizes_feedback_over_due_bank(self):
        before = self.queue(self.prior())
        session, data = self.start(before)
        self.assertEqual(data["selectedIds"], [10])
        bank = data["banks"][0]
        self.assertEqual(bank["point"], "identity-evidence", bank.get("reason"))
        ctx = revision.context(session, data, bank)
        self.assertEqual(ctx["sourceHead"], self.asset)
        self.assertEqual(ctx["request"], before["reviewFeedback"][0])
        self.assertEqual((session / "worktree" / PATH).read_bytes(), A)

    def test_manual_pr_absent_from_proposal_state_gets_revision_context(self):
        session, data = self.start(self.queue(None))
        self.assertEqual(data["banks"][0]["point"], "identity-evidence")
        self.assertEqual(revision.context(session, data, data["banks"][0])["mr"]["number"], 146)

    def test_pending_candidate_is_not_discarded_for_new_feedback(self):
        before = self.queue(self.prior())
        before["proposals"][-1].pop("mr")
        before["proposals"][-1]["status"] = "pending"
        point, eligible, _ = revision.selection(before, state.digest(A))
        self.assertEqual(point, "waiting-proposal"); self.assertFalse(eligible)

    def test_revision_replaces_one_active_proposal_but_retains_full_history(self):
        before = self.queue(self.prior())
        session, data = self.start(before)
        bank = data["banks"][0]
        payload = {"outcome": "candidate", "checkedAt": intake.now(), "iconHash": state.digest(A),
                   "candidate": {"iconHash": state.digest(C)}, "sources": [], "assessment": {}, "reason": "Selected variant"}
        after = revision.attempt_state(session, data, bank, before, payload)
        self.assertEqual(len(after["proposals"]), 1)
        self.assertEqual(after["proposals"][-1]["revisionHistory"], before["proposals"])
        self.assertEqual(after["reviewFeedback"][0]["status"], "candidate")
        self.assertEqual(after["lastVerifiedAt"], before["lastVerifiedAt"])
        self.assertEqual(before["proposals"][-1]["status"], "open")
        mr = {**bank["state"]["proposals"][-1]["mr"], "headCommit": "f" * 40}
        revision.published(after, mr, state.digest(C))
        self.assertEqual(after["reviewFeedback"][0]["status"], "published")
        self.assertIsNone(revision.queued(after))
        with self.assertRaisesRegex(ValueError, "another MR"):
            revision.published(after, {**mr, "number": 147}, state.digest(C))

    def test_failed_attachment_result_is_durable_without_reopening_search(self):
        before = self.queue(self.prior()); session, data = self.start(before); bank = data["banks"][0]
        payload = {"outcome": "unconfirmed", "checkedAt": intake.now(), "reason": "Attachment unavailable; original vector not obtained"}
        after = revision.attempt_state(session, data, bank, before, payload)
        self.assertEqual(after["proposals"], before["proposals"])
        self.assertEqual(after["reviewFeedback"][0]["status"], "blocked")
        self.assertIsNone(revision.queued(after))
        self.assertEqual(after["verifiedIconHash"], state.digest(A))

    def test_unchanged_cannot_claim_selected_attachment_was_satisfied(self):
        before = self.queue(self.prior()); session, data = self.start(before)
        with self.assertRaisesRegex(ValueError, "ordinary unchanged"):
            revision.attempt_state(session, data, data["banks"][0], before,
                                  {"outcome": "unchanged", "checkedAt": intake.now(), "reason": "Current icon"})

    def test_changed_head_and_wrong_target_cannot_resume_request(self):
        before = self.queue(self.prior())
        new = assets._commit(self.repo, self.root, [(PATH, C)], "human edit", self.asset)
        git(self.repo, "push", "-q", "origin", f"{new}:refs/heads/{HEAD}", f"{new}:refs/pull/146/head")
        with self.assertRaisesRegex(ValueError, "PR/head/description changed"):
            self.start(before)

    def test_full_intake_package_uses_existing_state_writer_without_search(self):
        scan_session = self.root / "intake"
        with patch.object(intake, "repository_from_remote", return_value=TARGET):
            scanned = intake.scan(argparse.Namespace(repo=str(self.repo), session=str(scan_session), base="main", remote=None))
        event = scanned["events"][0]
        path = scan_session / "decisions.json"; path.write_bytes(state.encoded([self.decision(event)]))
        prepared = intake.prepare(argparse.Namespace(session=str(scan_session), decisions=str(path)))
        package = prepared["package"]
        self.assertEqual(package["records"][0]["after"]["reviewFeedback"][0]["status"], "queued")
        root = scan_session / "state-publication"
        self.assertEqual(state.load_package(root, prepared["packageHash"]), package)
        objects = state.object_repo(root)
        state.validate_package(scan_session, package, root, objects, None)
        self.assertFalse((scan_session / "worktree").exists())
        self.assertEqual(self.comment_calls, 0)
        self.assertNotIn("lastVerifiedAt", package["records"][0]["after"])


if __name__ == "__main__":
    unittest.main()
