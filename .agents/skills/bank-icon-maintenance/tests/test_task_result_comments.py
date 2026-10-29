"""Result notices stay visible on GitHub, but are not new reviewer requirements.

Exercise the actual discussion reader, normalizer and live task check against a
controlled GitHub read boundary. No comments or other remote writes are sent.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import feedback_intake as intake
import github_pr
import rejection_review
import task_work as work

KEY = 'a' * 64
AT = '2026-01-02T03:04:05.123456Z'


def notice(key=KEY, at=AT):
    return (f'<!-- bank-icon-task-result:{key}:{at}:begin -->\n'
            'No publishable vector found; reviewer requirement remains unresolved.\n'
            f'<!-- bank-icon-task-result:{key}:{at}:end -->')


class TaskResultComments(unittest.TestCase):
    def test_only_generated_blocks_are_removed_and_raw_text_is_preserved(self):
        comparison = (f'<!-- bank-icon-feedback:{KEY}:begin -->comparison'
                      f'<!-- bank-icon-feedback:{KEY}:end -->')
        for at in (AT, '2026-01-02T03:04:05Z'):
            with self.subTest(at=at):
                raw = 'Keep full emblem.\n' + notice(at=at) + '\n' + comparison + '\nUse original colors.'
                before = raw
                self.assertEqual(intake.clean_body(notice(at=at)), '')
                self.assertEqual(intake.clean_body(raw), 'Keep full emblem.\n\n\nUse original colors.')
                self.assertEqual(raw, before)
        report = notice().replace('No publishable vector found;', '![reported](https://example.invalid/report.png);')
        self.assertEqual(intake.attachment_urls(report + '\n![requested](https://example.invalid/request.png)'),
                         ['https://example.invalid/request.png'])

    def test_incomplete_or_mismatched_markers_are_not_silently_hidden(self):
        original = notice()
        malformed = [original.split('\n')[:2],
                     original.replace(f'{KEY}:{AT}:end', f'{"b" * 64}:{AT}:end'),
                     original.replace(f'{AT}:end', '2026-01-03T03:04:05Z:end'),
                     original.replace('task-result:', 'feedback:', 1),
                     original.replace(AT, 'not-a-recorded-time')]
        for raw in malformed:
            raw = '\n'.join(raw) if isinstance(raw, list) else raw
            with self.subTest(raw=raw):
                self.assertEqual(intake.clean_body(raw), raw.strip())

    def test_result_comments_keep_task_key_and_live_check_but_human_reply_changes_them(self):
        target, number = 'fixture/catalog', 146
        url = f'https://github.com/{target}/pull/{number}'
        author = {'login': 'owner', 'type': 'User'}  # Agent may post using the owner's account.
        def row(node, body):
            return {'node_id': node, 'html_url': url + '#issuecomment-' + node,
                    'body': body, 'user': author}
        comments = [row('1', 'Preserve the complete emblem.')]
        def api(endpoint, *args):
            if endpoint == f'repos/{target}/pulls/{number}':
                return {'node_id': 'PR', 'html_url': url, 'body': 'Original proposal', 'user': author}
            if endpoint == f'repos/{target}/issues/{number}/comments':
                return copy.deepcopy(comments)
            if endpoint in (f'repos/{target}/pulls/{number}/reviews', f'repos/{target}/pulls/{number}/comments'):
                return []
            self.fail(f'Unexpected API call: {endpoint}')
        with patch.object(github_pr, '_api', side_effect=api):
            messages = work._messages(target, number)
            mr = {'number': number, 'url': url, 'state': 'open', 'baseCommit': 'b' * 40}
            for task in ('rework-changes', 'rework-rejected'):
                with self.subTest(task=task):
                    comments[:] = comments[:1]
                    mr['state'] = 'closed' if task == 'rework-rejected' else 'open'
                    value = {'task': task, 'bank': {'id': 10}, 'target': target, 'mr': mr,
                             'outcomes': [], 'messages': messages}
                    key = work._key(value)
                    # Two real attempts are distinct notices, neither is a fresh requirement.
                    comments.extend([row('2', notice(key=key)),
                                     row('3', notice(key=key, at='2026-01-03T03:04:05Z'))])
                    raw = rejection_review.read_thread(target, number)
                    self.assertEqual(next(r['body'] for r in raw if r['nodeId'] == '2'), comments[1]['body'])
                    self.assertEqual([r['nodeId'] for r in rejection_review.candidates({'messages': raw})], ['1'])
                    self.assertEqual(work._messages(target, number), messages)
                    self.assertEqual(work._key({**value, 'messages': work._messages(target, number)}), key)
                    with patch.object(github_pr, 'inspect', return_value=mr):
                        work.verify_live(value)
                        comments.append(row('4', 'Use the newly attached vector instead.'))
                        self.assertNotEqual(work._key({**value, 'messages': work._messages(target, number)}), key)
                        with self.assertRaisesRegex(ValueError, 'discussion changed'):
                            work.verify_live(value)


if __name__ == '__main__':
    unittest.main()
