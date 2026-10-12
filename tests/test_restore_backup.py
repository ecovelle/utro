import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('restore_backup', Path(__file__).resolve().parents[1] / 'scripts/restore_backup.py')
restore_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore_backup)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.previous_dir = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.previous_dir)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Backup test')
        self.git('config', 'user.email', 'backup@example.invalid')
        (self.root / 'index.html').write_text('old encrypted page')
        (self.root / '.github/workflows').mkdir(parents=True)
        (self.root / '.github/workflows/utro.yml').write_text('old workflow')
        self.git('add', '.')
        self.git('commit', '-m', 'Original')
        self.snapshot = self.git('rev-parse', 'HEAD')
        self.tree = self.git('rev-parse', 'HEAD^{tree}')
        (self.root / 'index.html').write_text('new encrypted page')
        (self.root / 'history.enc.json').write_text('new encrypted history')
        (self.root / '.github/workflows/utro.yml').write_text('new workflow')
        self.git('add', '.')
        self.git('commit', '-m', 'Redesign')
        self.new_head = self.git('rev-parse', 'HEAD')
        self.safety = 'codex/before-rollback-test'

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.PIPE).strip()

    def test_exact_tree_and_new_commit_with_history_preserved(self):
        restore_backup.restore(self.snapshot, self.tree, self.safety)
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}'), self.tree)
        self.assertEqual(self.git('rev-parse', 'HEAD^'), self.new_head)
        self.assertEqual(self.git('rev-parse', self.safety), self.new_head)
        self.assertEqual(self.git('show', self.safety + ':history.enc.json'), 'new encrypted history')
        self.assertFalse((self.root / 'history.enc.json').exists())
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_dirty_checkout_refused_before_changes(self):
        (self.root / 'unsaved.txt').write_text('keep')
        with self.assertRaisesRegex(RuntimeError, 'чистой'):
            restore_backup.restore(self.snapshot, self.tree, self.safety)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.new_head)

    def test_non_main_refused(self):
        self.git('checkout', '-b', 'feature')
        with self.assertRaisesRegex(RuntimeError, 'main'):
            restore_backup.restore(self.snapshot, self.tree, self.safety)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.new_head)

    def test_wrong_snapshot_tree_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'не совпало'):
            restore_backup.restore(self.snapshot, '0' * 40, self.safety)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.new_head)

    def test_atomic_push_preserves_backup_and_restores_main(self):
        remote = self.root.parent / (self.root.name + '-remote.git')
        self.addCleanup(lambda: __import__('shutil').rmtree(remote, ignore_errors=True))
        self.git('init', '--bare', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', 'origin', 'main')
        restore_backup.restore(self.snapshot, self.tree, self.safety, push=True)
        self.assertEqual(self.git('--git-dir=' + str(remote), 'rev-parse', self.safety), self.new_head)
        self.assertEqual(self.git('--git-dir=' + str(remote), 'rev-parse', 'main^{tree}'), self.tree)

    def test_concurrent_main_update_is_not_overwritten(self):
        import shutil
        remote = self.root.parent / (self.root.name + '-remote.git')
        writer = self.root.parent / (self.root.name + '-writer')
        self.addCleanup(lambda: shutil.rmtree(remote, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(writer, ignore_errors=True))
        self.git('init', '--bare', '-b', 'main', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', 'origin', 'main')
        self.git('clone', str(remote), str(writer))
        self.git('-C', str(writer), 'config', 'user.name', 'Concurrent writer')
        self.git('-C', str(writer), 'config', 'user.email', 'writer@example.invalid')
        (writer / 'new-file.txt').write_text('concurrent update')
        self.git('-C', str(writer), 'add', '.')
        self.git('-C', str(writer), 'commit', '-m', 'Concurrent update')
        latest = self.git('-C', str(writer), 'rev-parse', 'HEAD')
        self.git('-C', str(writer), 'push', 'origin', 'main')
        with self.assertRaises(subprocess.CalledProcessError):
            restore_backup.restore(self.snapshot, self.tree, self.safety, push=True)
        self.assertEqual(self.git('--git-dir=' + str(remote), 'rev-parse', 'main'), latest)
        # The atomic push also refuses to publish half of the operation.
        with self.assertRaises(subprocess.CalledProcessError):
            self.git('--git-dir=' + str(remote), 'rev-parse', '--verify', self.safety)


if __name__ == '__main__':
    unittest.main()
