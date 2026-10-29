"""Shared Unicode catalog fixture for current candidate/publication checks."""
from unittest.mock import patch
import test_candidates as candidates

ORIGINAL_CANDIDATE_SETUP = candidates.CandidateCLI.setUp


def unicode_candidate_setup(fixture):
    original_git = candidates.git
    renamed = False

    def rename_before_first_commit(repo, *args):
        nonlocal renamed
        if not renamed and repo == fixture.repo and args == ("add", "."):
            old = fixture.repo / "banks" / fixture.folder
            fixture.folder = "Álpha_10-ru"
            old.rename(fixture.repo / "banks" / fixture.folder)
            renamed = True
        return original_git(repo, *args)

    with patch.object(candidates, "git", rename_before_first_commit):
        ORIGINAL_CANDIDATE_SETUP(fixture)
