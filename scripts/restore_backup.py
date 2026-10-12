#!/usr/bin/env python3
"""Restore the verified pre-redesign tree in a new commit, preserving current HEAD."""
import argparse
import subprocess
from datetime import datetime, timezone

BACKUP_COMMIT = 'd7a516789e7a8be6fc2d2120b7a73219b5254610'
BACKUP_TREE = '9d0084e74282490ea2a23dd580e2d4b9cc151f6b'


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def restore(snapshot, expected_tree, safety_branch, push=False):
    if git('branch', '--show-current') != 'main':
        raise RuntimeError('Восстановление разрешено только из ветки main')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Сначала сохраните локальные изменения: рабочая папка должна быть чистой')
    git('check-ref-format', '--branch', safety_branch)
    if not safety_branch.startswith('codex/before-rollback-'):
        raise RuntimeError('Недопустимое имя страховочной ветки')
    if git('rev-parse', snapshot + '^{tree}') != expected_tree:
        raise RuntimeError('Дерево резервной копии не совпало с проверенным')
    previous = git('rev-parse', 'HEAD')
    git('branch', safety_branch, previous)
    # Restore tracked paths, including removing files introduced after the snapshot.
    # The safety branch retains both new code and any accumulated encrypted history.
    git('restore', '--source=' + snapshot, '--staged', '--worktree', '--', '.')
    if git('write-tree') != expected_tree:
        raise RuntimeError('Проверка восстановленного дерева не пройдена; публикация остановлена')
    if git('diff', '--cached', '--name-only'):
        git('commit', '-m', 'Restore verified backup before redesign (2026-10-12)')
    if push:
        # No force push: if main moved meanwhile, neither ref is published.
        git('push', '--atomic', 'origin', safety_branch, 'HEAD:refs/heads/main')
    print('Код, workflow и зашифрованная страница восстановлены.')
    print('Версия до восстановления сохранена в ' + safety_branch)
    return previous


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--push', action='store_true', help='Опубликовать обычным push без перезаписи истории')
    parser.add_argument('--safety-branch', default='codex/before-rollback-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    args = parser.parse_args()
    restore(BACKUP_COMMIT, BACKUP_TREE, args.safety_branch, args.push)
