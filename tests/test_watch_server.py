import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import watch_server


def git(directory, *args):
    return subprocess.run(['git', *args], cwd=directory, check=True, capture_output=True, text=True)


class AutoUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source = root / 'source'
        self.remote = root / 'origin.git'
        self.clone = root / 'working-copy'
        self.source.mkdir()
        git(self.source, 'init', '-b', 'main')
        git(self.source, 'config', 'user.email', 'test@example.com')
        git(self.source, 'config', 'user.name', 'Test Runner')
        (self.source / 'server.py').write_text('version = 1\n', encoding='utf-8')
        git(self.source, 'add', 'server.py')
        git(self.source, 'commit', '-m', 'initial')
        git(root, 'clone', '--bare', str(self.source), str(self.remote))
        git(self.source, 'remote', 'add', 'origin', str(self.remote))
        git(self.source, 'push', '-u', 'origin', 'main')
        git(root, 'clone', str(self.remote), str(self.clone))

    def tearDown(self):
        self.temp.cleanup()

    def publish_update(self):
        (self.source / 'server.py').write_text('version = 2\n', encoding='utf-8')
        git(self.source, 'add', 'server.py')
        git(self.source, 'commit', '-m', 'update')
        git(self.source, 'push', 'origin', 'main')

    def test_automatically_fast_forwards_clean_copy(self):
        self.publish_update()
        with patch.object(watch_server, 'ROOT', self.clone):
            updated, message = watch_server.check_for_updates()
        self.assertTrue(updated)
        self.assertEqual((self.clone / 'server.py').read_text(encoding='utf-8'), 'version = 2\n')
        self.assertEqual(message, git(self.clone, 'rev-parse', 'HEAD').stdout.strip())

    def test_preserves_local_edits_and_skips_pull(self):
        self.publish_update()
        (self.clone / 'server.py').write_text('local change\n', encoding='utf-8')
        with patch.object(watch_server, 'ROOT', self.clone):
            updated, message = watch_server.check_for_updates()
        self.assertFalse(updated)
        self.assertIn('alterações locais', message)
        self.assertEqual((self.clone / 'server.py').read_text(encoding='utf-8'), 'local change\n')


if __name__ == '__main__':
    unittest.main()
