"""Old publication authority cannot silently expand to mutation of existing PRs."""
import unittest
from test_standing_authorization import PolicyFixture
import feedback_publication as publication


class RevisionAuthority(PolicyFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.asset['revision'] = {'requestKey': 'a'*64}
        self.asset['materials']['ref'] = publication.material_ref(self.asset['head'], 'a'*64)

    def test_new_mr_only_policy_does_not_authorize_revision(self):
        with self.assertRaisesRegex(ValueError, 'does not allow'):
            self.authorize(self.asset, 'assetPublish')

    def test_owner_opt_in_binds_exact_revision_and_material_ref(self):
        self.grant['actions']['assetRevise'] = True
        self.write_grant()
        use = self.authorize(self.asset, 'assetPublish')
        self.assertEqual(use['refs'], [self.asset['headRef'], self.asset['materials']['ref']])
        self.assertEqual(use['action'], 'assetPublish')

    def test_revocation_or_non_boolean_does_not_grant_permission(self):
        for flag in (False, 'true', 1):
            self.grant['actions']['assetRevise'] = flag
            self.write_grant()
            with self.assertRaises(ValueError): self.authorize(self.asset, 'assetPublish')

    def test_opt_in_does_not_expand_target_or_branch_scope(self):
        self.grant['actions']['assetRevise'] = True
        self.write_grant()
        self.asset['materials']['ref'] += '-different'
        with self.assertRaisesRegex(ValueError, 'outside standing'):
            self.authorize(self.asset, 'assetPublish')
