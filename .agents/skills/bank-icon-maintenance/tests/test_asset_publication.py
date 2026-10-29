"""Asset CLI against disposable Git and controlled gh protocol, not hosted PR proof."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from unittest.mock import patch
import test_candidates as candidates


class AssetPublicationCLI(unittest.TestCase):
    base_icon = candidates.BASE
    raw_source = candidates.RAW
    setUp = candidates.CandidateCLI.setUp
    cli = candidates.CandidateCLI.cli
    file = candidates.CandidateCLI.file
    create_package = candidates.CandidateCLI.create_package

    def test_unapproved_candidate_cannot_create_asset_worktree_or_commit(self):
        self.cli("continue", "--candidate", str(self.package_path))
        result = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                          "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertFalse((self.session / "asset-publication/10/worktree").exists())


# This executable deliberately supplies controlled protocol responses. It is installed
# ONLY in a per-test PATH; all refs and commits under assertion come from real Git.
GH = r'''#!PYTHON
import json
import os
from pathlib import Path
import subprocess
import sys

args = sys.argv[1:]
if args[:3] != ['api', '--hostname', 'github.com']:
    sys.exit('unsupported gh command')
endpoint = args[3]
store = Path(os.environ['FIXTURE_GH_STATE'])
state = json.loads(store.read_text())
if endpoint == 'graphql':
    print(json.dumps({'data': {'repository': {'nameWithOwner': 'fixture/catalog',
        'url': 'https://github.com/fixture/catalog', 'isPrivate': True,
        'defaultBranchRef': {'name': 'main'}, 'viewerPermission': 'ADMIN'}}}))
elif endpoint == 'repos/fixture/catalog/pulls' and '-X' in args and args[args.index('-X')+1] == 'GET':
    if state.get('lookupError'):
        sys.exit('controlled gh transport failure')
    fields = dict(part.split('=', 1) for flag, part in zip(args, args[1:]) if flag == '-f')
    if fields.get('state') == 'open':
        rows = [row for row in state['prs'] if row['state'] == 'open']
    else:
        owner, branch = fields['head'].split(':', 1)
        rows = [row for row in state['prs'] if row['head']['ref'] == branch and
                row['head']['repo']['full_name'].split('/')[0] == owner]
    print(json.dumps([rows]))
elif endpoint.startswith('repos/fixture/catalog/pulls/') and endpoint.endswith('/files'):
    number = endpoint.split('/')[-2]
    print(json.dumps([state.get('files', {}).get(number, [])]))
elif endpoint == 'repos/fixture/catalog/pulls' and '-X' in args and args[args.index('-X')+1] == 'POST':
    state['createCalls'] += 1
    fields = dict(part.split('=', 1) for flag, part in zip(args, args[1:]) if flag == '-f')
    def ref(name):
        return subprocess.check_output(['git', '--git-dir', os.environ['FIXTURE_GIT_REMOTE'],
            'rev-parse', 'refs/heads/' + name], text=True).strip()
    row = {'number': 42, 'html_url': 'https://github.com/fixture/catalog/pull/42',
        'state': 'open', 'merged_at': None, 'title': fields['title'], 'body': fields['body'],
        'head': {'ref': fields['head'], 'sha': ref(fields['head']),
                 'repo': {'full_name': 'fixture/catalog'}},
        'base': {'ref': fields['base'], 'sha': ref(fields['base']),
                 'repo': {'full_name': 'fixture/catalog'}}}
    if state.get('createUnknown') == 'before':
        store.write_text(json.dumps(state))
        sys.exit('controlled failure before PR creation')
    state['prs'].append(row)
    store.write_text(json.dumps(state))
    if state.get('createUnknown') == 'after':
        sys.exit('controlled response lost after PR creation')
    print(json.dumps(row))
else:
    sys.exit('unsupported gh endpoint')
'''


class ApprovedAssetCLI(unittest.TestCase):
    def setUp(self):
        fixture = candidates.CandidateCLI(
            "test_preparation_preserves_original_report_and_current_icon_pending_human_review")
        self.addCleanup(fixture.doCleanups)
        fixture.base_icon = getattr(self, "base_icon", candidates.BASE)
        fixture.setUp()
        self.fixture = fixture
        self.session, self.repo, self.root = fixture.session, fixture.repo, fixture.root
        self.folder = fixture.folder
        prepared = fixture.cli("continue", "--candidate", str(fixture.package_path))["banks"][0]
        agent = getattr(self, "agent_assessment", False)
        provenance = fixture.file("reviewer.txt", b"Compared exact light and dark renders, source and original icon")
        receipt = {"schemaVersion": 1, "scope": "candidate-assessment" if agent else "candidate-review",
                   "decision": "suitable" if agent else "approve", "id": 10,
                   "baseCommit": fixture.started["baseCommit"],
                   "baseIconHash": fixture.started["banks"][0]["iconHash"],
                   "candidateIconHash": candidates.digest(candidates.FINAL),
                   "reviewPackage": prepared["state"]["proposals"][-1]["reviewPackage"],
                   "assessor" if agent else "reviewer": "Agent" if agent else "Human reviewer",
                   "statement": "Compared source, base and final on light and dark backgrounds",
                   "assessedAt" if agent else "reviewedAt": "2026-09-25T12:00:00Z",
                   "provenance": provenance}
        approval = fixture.evidence / "approval.json"
        candidates.put_json(approval, receipt)
        fixture.cli("candidate-assess" if agent else "candidate-approve", "--id", "10",
                    "--assessment" if agent else "--approval", str(approval))
        self.remote = self.root / "remote.git"
        candidates.git(self.repo, "clone", "--bare", "-q", str(self.repo), str(self.remote))
        target = self.root / "target"
        candidates.git(self.repo, "clone", "-q", str(self.repo), str(target))
        self.target = target
        candidates.git(target, "branch", "-M", "main")
        candidates.git(target, "config", "user.name", "Test")
        candidates.git(target, "config", "user.email", "test@example.invalid")
        (target / "unrelated.txt").write_text("new target-base content\n")
        candidates.git(target, "add", "unrelated.txt")
        candidates.git(target, "commit", "-qm", "unrelated target base")
        self.target_base = candidates.git(target, "rev-parse", "HEAD")
        candidates.git(target, "push", "-q", str(self.remote), "HEAD:refs/heads/main")
        self.global_config = self.root / "gitconfig"
        candidates.git(self.repo, "config", "--file", str(self.global_config),
                       f"url.file://{self.remote}.insteadOf", "https://github.com/fixture/catalog.git")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        script = self.bin / "gh"
        script.write_text(GH.replace("#!PYTHON", "#!" + sys.executable))
        script.chmod(0o755)
        self.gh_state = self.root / "gh.json"
        self.gh_state.write_text(json.dumps({"prs": [], "createCalls": 0}))
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "GIT_CONFIG_GLOBAL": str(self.global_config), "GIT_CONFIG_NOSYSTEM": "1",
                    "FIXTURE_GH_STATE": str(self.gh_state), "FIXTURE_GIT_REMOTE": str(self.remote),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        import admission_fixture
        report = json.loads((self.session / prepared["report"]).read_text())
        self.admission_path = admission_fixture.save(self.session, report)

    def cli(self, command, *args, ok=True):
        # Explicit synthetic preflight supplied by the fixture, not a runtime bypass.
        if command == "asset-prepare" and "--admission" not in args and getattr(self, "admission_path", None):
            args = (*args, "--admission", self.admission_path)
        result = subprocess.run([sys.executable, str(candidates.SCRIPT), command,
                                 "--session", str(self.session), *args],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def prepare(self):
        return self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                        "--base", "main", "--head", getattr(self, "head", "icons/10-reviewed"))

    def remote_ref(self, name):
        result = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "--verify",
                                 "refs/heads/" + name], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def test_approved_candidate_applies_only_exact_icon_on_target_base(self):
        frozen_head = candidates.git(self.session / "worktree", "rev-parse", "HEAD")
        prepared = self.prepare()
        package = prepared["package"]
        self.assertEqual(package["baseCommit"], self.target_base)
        self.assertNotEqual(package["baseCommit"], self.fixture.started["baseCommit"])
        worktree = self.session / "asset-publication/10/worktree"
        self.assertEqual((worktree / "banks" / self.folder / "icon.svg").read_bytes(), candidates.FINAL)
        self.assertEqual((worktree / "unrelated.txt").read_text(), "new target-base content\n")
        self.assertEqual(candidates.git(worktree, "diff", "--name-only", "HEAD^", "HEAD"),
                         f"banks/{self.folder}/icon.svg")
        self.assertEqual(candidates.git(self.session / "worktree", "rev-parse", "HEAD"), frozen_head)
        self.assertEqual((self.session / "worktree/banks" / self.folder / "icon.svg").read_bytes(),
                         candidates.BASE)
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertIsNone(self.remote_ref("icons/10-reviewed-review"))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)


    def authorization(self, package, **changes):
        receipt = {"schemaVersion": 1, "scope": "asset-publication", "decision": "authorize",
                   "packageHash": self.package_hash,
                   "target": package["target"], "id": 10, "base": package["base"],
                   "baseCommit": package["baseCommit"], "head": package["head"],
                   "assetCommit": package["assetCommit"],
                   "materialRef": package["materials"]["ref"],
                   "materialCommit": package["materials"]["commit"],
                   "reviewer": "Human reviewer", "statement": "Authorize exact private fixture package",
                   "authorizedAt": "2026-09-25T12:01:00Z"}
        receipt.update(changes)
        path = self.root / "asset-authorization.json"
        candidates.put_json(path, receipt)
        return path

    def test_review_commit_contains_only_public_materials_and_commit_pinned_links(self):
        prepared = self.prepare()
        package = prepared["package"]
        names = candidates.git(self.session / "asset-publication/objects.git", "ls-tree", "-r",
                               "--name-only", package["materials"]["commit"]).splitlines()
        self.assertEqual(names, ["review-materials/10/" + name for name in sorted((
            "base.svg", "source.svg", "final.svg", "candidate.diff", "final.xml",
            "comparison-light.png", "comparison-dark.png"))])
        self.assertIn(package["materials"]["commit"], package["body"])
        self.assertIn("https://github.com/fixture/catalog", package["body"])
        self.assertNotIn(str(self.root), package["body"])
        self.assertNotIn("file://", package["body"])

    def test_missing_or_wrong_publication_authorization_leaves_refs_and_pr_absent(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        waiting = self.cli("asset-publish", "--package", self.package_hash)
        self.assertEqual(waiting["status"], "awaiting-asset-authorization")
        authorization = self.authorization(prepared["package"], assetCommit="0" * 40)
        result = self.cli("asset-publish", "--package", self.package_hash,
                          "--authorization", str(authorization), ok=False)
        self.assertIn("authorization", result["error"])
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertIsNone(self.remote_ref("icons/10-reviewed-review"))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)

    def provider_state(self, **changes):
        state = json.loads(self.gh_state.read_text())
        state.update(changes)
        self.gh_state.write_text(json.dumps(state))

    def test_unavailable_provider_is_not_interpreted_as_no_pr(self):
        self.provider_state(lookupError=True)
        result = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                          "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertIn("GitHub API request failed", result["error"])
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertFalse((self.session / "asset-publication/10/worktree").exists())

    def test_changed_candidate_or_target_base_requires_new_review_before_publication(self):
        (self.fixture.evidence / "final.svg").write_bytes(b"<svg>unreviewed</svg>")
        rejected = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                            "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertIn("error", rejected)
        self.assertFalse((self.session / "asset-publication/10/worktree").exists())
        (self.fixture.evidence / "final.svg").write_bytes(candidates.FINAL)
        icon = self.target / "banks" / self.folder / "icon.svg"
        icon.write_bytes(b"<svg>different accepted icon</svg>")
        candidates.git(self.target, "add", ".")
        candidates.git(self.target, "commit", "-qm", "independent target icon change")
        candidates.git(self.target, "push", "-q", str(self.remote), "HEAD:refs/heads/main")
        rejected = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                            "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertFalse((self.session / "asset-publication/10/worktree").exists())
        self.assertEqual(icon.read_bytes(), b"<svg>different accepted icon</svg>")
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)

    def test_existing_closed_or_merged_pr_blocks_same_head_but_unrelated_head_does_not(self):
        candidates.git(self.remote, "update-ref", "refs/heads/icons/10-reviewed", self.target_base)
        row = {"number": 8, "html_url": "https://github.com/fixture/catalog/pull/8",
               "state": "closed", "merged_at": None, "title": "Prior bank proposal",
               "body": "Complete prior proposal with its own evidence",
               "head": {"ref": "icons/10-reviewed", "sha": self.remote_ref("icons/10-reviewed"),
                        "repo": {"full_name": "fixture/catalog"}},
               "base": {"ref": "main", "sha": self.target_base,
                        "repo": {"full_name": "fixture/catalog"}}}
        for merged in (None, "2026-09-25T12:00:00Z"):
            with self.subTest(merged=merged):
                self.provider_state(prs=[{**row, "merged_at": merged}])
                result = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                                  "--base", "main", "--head", "icons/10-reviewed", ok=False)
                self.assertIn("provider history", result["error"])
                self.assertFalse((self.session / "asset-publication/10/worktree").exists())
        candidates.git(self.remote, "update-ref", "-d", "refs/heads/icons/10-reviewed")
        self.provider_state(prs=[{**row, "head": {**row["head"], "ref": "other-bank"}}])
        self.assertEqual(self.prepare()["status"], "prepared")
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)

    def test_open_pr_from_another_head_touching_same_icon_blocks_new_proposal(self):
        candidates.git(self.remote, "update-ref", "refs/heads/icons/older", self.target_base)
        row = {"number": 17, "html_url": "https://github.com/fixture/catalog/pull/17",
               "state": "open", "merged_at": None, "title": "Earlier icon proposal",
               "body": "Already under human review",
               "head": {"ref": "icons/older", "sha": self.remote_ref("icons/older"),
                        "repo": {"full_name": "fixture/catalog"}},
               "base": {"ref": "main", "sha": self.target_base,
                        "repo": {"full_name": "fixture/catalog"}}}
        self.provider_state(prs=[row], files={"17": [{"filename": f"banks/{self.folder}/icon.svg"}]})
        result = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                          "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertIn("open", result["error"].lower())
        self.assertFalse((self.session / "asset-publication/10/worktree").exists())
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)


    def test_reconcile_partial_two_ref_push_then_resume_without_duplicate_pr(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        package = prepared["package"]
        authorization = self.authorization(package)
        hook = self.remote / "hooks/pre-receive"
        hook.write_text(chr(10).join(("#!/bin/sh", "while read old new ref; do",
            '  if [ "$ref" = refs/heads/icons/10-reviewed ]; then',
            '    echo "fixture: branch rejected by policy" >&2', "    exit 1", "  fi",
            "done", "exit 0")) + chr(10))
        hook.chmod(0o700)
        partial = self.cli("asset-publish", "--package", self.package_hash,
                           "--authorization", str(authorization))
        self.assertEqual(partial["status"], "publication-failed")
        self.assertIn("fixture: branch rejected by policy", partial["error"])
        self.assertTrue(partial["materialPublished"])
        self.assertFalse(partial["assetPublished"])
        self.assertEqual(self.remote_ref("icons/10-reviewed-review"), package["materials"]["commit"])
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)
        hook.unlink()
        observed = self.cli("asset-reconcile", "--package", self.package_hash)
        self.assertEqual(observed["status"], "not-published")
        resumed = self.cli("asset-publish", "--package", self.package_hash,
                           "--authorization", str(authorization))
        self.assertEqual(resumed["status"], "mr-confirmed")
        self.assertEqual(self.remote_ref("icons/10-reviewed"), package["assetCommit"])
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)


    def test_authorized_two_ref_publish_records_one_confirmed_pr_without_state_publication(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        package = prepared["package"]
        state_before = self.remote_ref("banks-icon-agent-state")
        result = self.cli("asset-publish", "--package", self.package_hash,
                          "--authorization", str(self.authorization(package)))
        self.assertEqual(result["status"], "mr-confirmed")
        self.assertEqual(self.remote_ref(package["head"]), package["assetCommit"])
        self.assertEqual(self.remote_ref(package["head"] + "-review"), package["materials"]["commit"])
        self.assertEqual(self.remote_ref("banks-icon-agent-state"), state_before)
        self.assertEqual(result["mr"]["headCommit"], package["assetCommit"])
        self.assertEqual(result["mr"]["baseCommit"], self.target_base)
        self.assertEqual(result["mr"]["url"], "https://github.com/fixture/catalog/pull/42")
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "waiting-mr")
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)
        repeated = self.cli("asset-publish", "--package", self.package_hash,
                            "--authorization", str(self.authorization(package)))
        self.assertEqual(repeated["status"], "mr-confirmed")
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)

    def test_saved_mr_provider_change_blocks_state_prepare_until_reconciled(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        self.cli("asset-publish", "--package", self.package_hash,
                 "--authorization", str(self.authorization(prepared["package"])))
        previous = self.remote_ref("banks-icon-agent-state")
        observation = json.loads(self.gh_state.read_text())["prs"][0]
        changes = ({"body": "A different unreviewed description"},
                   {"head": {**observation["head"], "sha": "0" * 40}},
                   {"state": "closed"})
        for change in changes:
            with self.subTest(change=change):
                self.provider_state(prs=[{**observation, **change}])
                result = self.cli("state-prepare", "--remote", str(self.remote),
                                  "--ids", "10", ok=False)
                self.assertIn("error", result)
                self.assertEqual(self.remote_ref("banks-icon-agent-state"), previous)
        self.provider_state(prs=[observation])


    def test_confirmed_pr_requires_new_independent_state_authorization(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        asset_authorization = self.authorization(prepared["package"])
        self.assertEqual(self.cli("asset-publish", "--package", self.package_hash,
                                  "--authorization", str(asset_authorization))["status"], "mr-confirmed")
        prior = self.remote_ref("banks-icon-agent-state")
        state = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertEqual(self.cli("state-publish", "--package", state["packageHash"])["status"],
                         "awaiting-state-authorization")
        rejected = self.cli("state-publish", "--package", state["packageHash"],
                            "--authorization", str(asset_authorization), ok=False)
        self.assertIn("authorization", rejected["error"])
        self.assertEqual(self.remote_ref("banks-icon-agent-state"), prior)
        receipt = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                   "packageHash": state["packageHash"], "target": state["package"]["target"],
                   "ref": "refs/heads/banks-icon-agent-state", "ids": [10],
                   "reviewer": "Human reviewer", "statement": "Authorize exact state package separately",
                   "authorizedAt": "2026-09-25T12:02:00Z"}
        path = self.root / "state-authorization.json"
        candidates.put_json(path, receipt)
        published = self.cli("state-publish", "--package", state["packageHash"],
                             "--authorization", str(path))
        self.assertEqual(published["status"], "published")
        self.assertNotEqual(self.remote_ref("banks-icon-agent-state"), prior)
        stored = json.loads(candidates.git(self.remote, "show",
            "refs/heads/banks-icon-agent-state:banks/state/10.json"))
        self.assertEqual(stored["proposals"][-1]["status"], "open")
        self.assertEqual(stored["proposals"][-1]["mr"]["number"], 42)
        self.assertEqual(stored["lastVerifiedAt"], "2025-01-01T00:00:00Z")


    def test_lost_create_response_reconciles_observed_pr_without_retry(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        self.provider_state(createUnknown="after")
        authorization = self.authorization(prepared["package"])
        result = self.cli("asset-publish", "--package", self.package_hash,
                          "--authorization", str(authorization))
        self.assertEqual(result["status"], "mr-confirmed")
        self.assertEqual(result["mr"]["headCommit"], prepared["package"]["assetCommit"])
        self.assertEqual(len(json.loads(self.gh_state.read_text())["prs"]), 1)
        self.assertEqual(self.cli("asset-reconcile", "--package", self.package_hash)["status"],
                         "mr-confirmed")
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)

    def test_unobservable_create_outcome_keeps_pushed_refs_and_never_retries_create(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        self.provider_state(createUnknown="before")
        authorization = self.authorization(prepared["package"])
        uncertain = self.cli("asset-publish", "--package", self.package_hash,
                             "--authorization", str(authorization))
        self.assertEqual(uncertain["status"], "outcome-unknown")
        self.assertEqual(self.remote_ref(prepared["package"]["head"]), prepared["package"]["assetCommit"])
        self.assertEqual(self.remote_ref(prepared["package"]["head"] + "-review"),
                         prepared["package"]["materials"]["commit"])
        repeated = self.cli("asset-publish", "--package", self.package_hash,
                            "--authorization", str(authorization))
        self.assertEqual(repeated["status"], "outcome-unknown")
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)


    def test_state_push_failure_preserves_confirmed_pr_and_recovers_with_separate_authorization(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        self.cli("asset-publish", "--package", self.package_hash,
                 "--authorization", str(self.authorization(prepared["package"])))
        state = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        prior = self.remote_ref("banks-icon-agent-state")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text(chr(10).join(("#!/bin/sh", "while read old new ref; do",
            '  [ "$ref" = refs/heads/banks-icon-agent-state ] && exit 1', "done", "exit 0")) + chr(10))
        hook.chmod(0o700)
        receipt = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                   "packageHash": state["packageHash"], "target": state["package"]["target"],
                   "ref": "refs/heads/banks-icon-agent-state", "ids": [10],
                   "reviewer": "Human reviewer", "statement": "First separate state decision",
                   "authorizedAt": "2026-09-25T12:02:00Z"}
        path = self.root / "state-authorization.json"
        candidates.put_json(path, receipt)
        failed = self.cli("state-publish", "--package", state["packageHash"],
                          "--authorization", str(path))
        self.assertEqual(failed["status"], "publication-failed")
        self.assertEqual(self.remote_ref("banks-icon-agent-state"), prior)
        self.assertEqual(self.remote_ref(prepared["package"]["head"]), prepared["package"]["assetCommit"])
        self.assertEqual(self.cli("asset-reconcile", "--package", self.package_hash)["status"],
                         "mr-confirmed")
        hook.unlink()
        candidates.put_json(path, {**receipt, "statement": "Fresh state publication decision after failure",
                                   "authorizedAt": "2026-09-25T12:03:00Z"})
        published = self.cli("state-publish", "--package", state["packageHash"],
                             "--authorization", str(path))
        self.assertEqual(published["status"], "published")
        self.assertNotEqual(self.remote_ref("banks-icon-agent-state"), prior)
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)


    def test_state_ref_retry_rechecks_live_mr_after_unrelated_race(self):
        import shlex

        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        self.cli("asset-publish", "--package", self.package_hash,
                 "--authorization", str(self.authorization(prepared["package"])))
        state = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        approval = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                    "packageHash": state["packageHash"], "target": state["package"]["target"],
                    "ref": "refs/heads/banks-icon-agent-state", "ids": [10],
                    "reviewer": "Human reviewer", "statement": "Authorize exact state package",
                    "authorizedAt": "2026-09-25T12:02:00Z"}
        approval_path = self.root / "state-race-approval.json"
        candidates.put_json(approval_path, approval)
        racer = self.root / "racer"
        candidates.git(self.repo, "clone", "-q", str(self.remote), str(racer))
        candidates.git(racer, "checkout", "-q", "banks-icon-agent-state")
        candidates.git(racer, "config", "user.name", "Test")
        candidates.git(racer, "config", "user.email", "test@example.invalid")
        candidates.put_json(racer / "banks/state/999.json", {"schemaVersion": 1,
            "lastAttemptAt": "2026-09-25T12:00:00Z", "lastAttemptResult": "unconfirmed"})
        candidates.git(racer, "add", ".")
        candidates.git(racer, "commit", "-qm", "unrelated bank state race")
        race_commit = candidates.git(racer, "rev-parse", "HEAD")
        change = ("import json,sys; p=sys.argv[1]; s=json.load(open(p)); "
                  "s['prs'][0]['state']='closed'; open(p,'w').write(json.dumps(s))")
        hook = self.session / "state-publication/objects.git/hooks/pre-push"
        hook.write_text(chr(10).join(("#!/bin/sh", "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE",
            shlex.join(["git", "-C", str(racer), "push", "-q", str(self.remote),
                        "HEAD:refs/heads/banks-icon-agent-state"]) + " || exit 1",
            shlex.join([sys.executable, "-c", change, str(self.gh_state)]) + " || exit 1",
            "exit 0")) + chr(10))
        hook.chmod(0o700)
        stopped = self.cli("state-publish", "--package", state["packageHash"],
                           "--authorization", str(approval_path), ok=False)
        self.assertIn("MR", stopped["error"])
        self.assertEqual(self.remote_ref("banks-icon-agent-state"), race_commit)
        self.assertEqual(self.remote_ref(prepared["package"]["head"]), prepared["package"]["assetCommit"])
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)


class FirstIconPublicationCLI(unittest.TestCase):
    base_icon = None
    setUp = ApprovedAssetCLI.setUp
    cli = ApprovedAssetCLI.cli
    prepare = ApprovedAssetCLI.prepare
    authorization = ApprovedAssetCLI.authorization
    remote_ref = ApprovedAssetCLI.remote_ref

    def test_exact_one_path_addition_preserves_metadata_and_source_checkout(self):
        metadata = (self.session / "worktree/banks" / self.folder / "info.json").read_bytes()
        prepared = self.prepare()
        package = prepared["package"]
        self.assertIsNone(package["baseIconHash"])
        worktree = self.session / "asset-publication/10/worktree"
        self.assertEqual(candidates.git(worktree, "diff", "--name-status", "HEAD^", "HEAD"),
                         f"A\tbanks/{self.folder}/icon.svg")
        self.assertIn("new file mode 100644", package["diff"])
        self.assertIn("--- /dev/null", package["diff"])
        self.assertEqual((worktree / "banks" / self.folder / "info.json").read_bytes(), metadata)
        self.assertEqual((worktree / "banks" / self.folder / "icon.svg").read_bytes(), candidates.FINAL)
        self.assertFalse((self.session / "worktree/banks" / self.folder / "icon.svg").exists())
        self.assertEqual(self.cli("asset-show", "--package", prepared["packageHash"])["package"], package)
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))

    def test_appeared_target_icon_blocks_preparation_without_overwrite(self):
        path = self.target / "banks" / self.folder / "icon.svg"
        path.write_bytes(candidates.BASE)
        candidates.git(self.target, "add", str(path))
        candidates.git(self.target, "commit", "-qm", "independent first icon")
        candidates.git(self.target, "push", "-q", str(self.remote), "HEAD:refs/heads/main")
        rejected = self.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                            "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertIn("error", rejected)
        self.assertEqual(path.read_bytes(), candidates.BASE)
        self.assertFalse((self.session / "asset-publication/10/worktree").exists())
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))

    def test_appeared_target_after_prepare_blocks_publication(self):
        prepared = self.prepare()
        self.package_hash = prepared["packageHash"]
        path = self.target / "banks" / self.folder / "icon.svg"
        path.write_bytes(candidates.BASE)
        candidates.git(self.target, "add", str(path))
        candidates.git(self.target, "commit", "-qm", "independent first icon")
        candidates.git(self.target, "push", "-q", str(self.remote), "HEAD:refs/heads/main")
        self.cli("asset-publish", "--package", self.package_hash,
                 "--authorization", str(self.authorization(prepared["package"])), ok=False)
        self.assertIsNone(self.remote_ref("icons/10-reviewed"))
        self.assertIsNone(self.remote_ref("icons/10-reviewed-review"))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)
        self.assertEqual(path.read_bytes(), candidates.BASE)


class UnicodeFirstIconPublicationCLI(unittest.TestCase):
    base_icon = None
    cli = ApprovedAssetCLI.cli
    prepare = ApprovedAssetCLI.prepare
    authorization = ApprovedAssetCLI.authorization
    remote_ref = ApprovedAssetCLI.remote_ref

    def setUp(self):
        from candidate_fixture import unicode_candidate_setup
        with patch.object(candidates.CandidateCLI, "setUp", unicode_candidate_setup):
            ApprovedAssetCLI.setUp(self)

    def test_unicode_first_icon_remains_exact_addition_through_publication(self):
        prepared = self.prepare()
        package = prepared["package"]
        self.package_hash = prepared["packageHash"]
        objects = self.session / "asset-publication/objects.git"
        changes = subprocess.run(["git", "--git-dir", str(objects), "diff", "--name-status", "-z",
                                  package["baseCommit"], package["assetCommit"]],
                                 capture_output=True, check=True).stdout
        self.assertEqual(changes.split(b"\0"), [b"A", f"banks/{self.folder}/icon.svg".encode(), b""])
        self.assertEqual(self.cli("asset-show", "--package", self.package_hash)["package"], package)
        published = self.cli("asset-publish", "--package", self.package_hash,
                             "--authorization", str(self.authorization(package)))
        self.assertEqual(published["status"], "mr-confirmed")
        self.assertEqual(self.remote_ref("icons/10-reviewed"), package["assetCommit"])
        self.assertFalse((self.session / "worktree/banks" / self.folder / "icon.svg").exists())


if __name__ == "__main__":
    unittest.main()
