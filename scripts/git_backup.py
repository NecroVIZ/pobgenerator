"""Git-бэкап проекта через dulwich (pure-python git; не требует системного git).

Команды:
  init   — инициализировать локальный репо (один раз)
  save   — add + commit текущего состояния (с уважением к .gitignore)
  status — показать изменения с последнего коммита
  log    — показать историю коммитов
  push REMOTE [URL] — push к remote (нужен PAT в URL или ~/.git-credentials)

REMOTE для NecroVIZ/pobgenerator:
  https://<PAT>@github.com/NecroVIZ/pobgenerator.git

PAT (Personal Access Token) создаётся: GitHub → Settings → Developer settings →
Personal access tokens → Tokens (classic) → Generate new token (scope: repo).
Вставлять PAT в URL — небезопасно для логов; альтернатива: git credential helper
(но без системного git это сложнее). Для разового push — PAT в URL, потом сменить.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dulwich import porcelain

REPO = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "origin"
DEFAULT_URL = "https://github.com/NecroVIZ/pobgenerator.git"


def _author() -> tuple[str, str]:
    """Git-identity: берём из окружения или дефолт."""
    name = os.environ.get("GIT_AUTHOR_NAME") or os.environ.get("GIT_COMMITTER_NAME") or "poebuildgen"
    email = os.environ.get("GIT_AUTHOR_EMAIL") or os.environ.get("GIT_COMMITTER_EMAIL") or "poebuildgen@local"
    return name, email


def cmd_init() -> None:
    if (REPO / ".git").exists():
        print(f"repo already exists at {REPO}")
        return
    porcelain.init(str(REPO))
    print(f"initialized git repo at {REPO}")


def _collect_paths(repo_root: Path) -> list[str]:
    """Собрать все файлы, не исключённые .gitignore (dulwich уважает ignore-filter)."""
    from dulwich.ignore import IgnoreFilter
    from dulwich.repo import Repo

    repo = Repo(str(repo_root))
    # IgnoreFilterManager — корректный способ (читает .gitignore рекурсивно)
    try:
        from dulwich.ignore import IgnoreFilterManager
        mgr = IgnoreFilterManager.from_repo(repo)
    except ImportError:
        mgr = None

    paths: list[str] = []
    git_dir = repo_root / ".git"
    for root, dirs, files in os.walk(repo_root):
        root_p = Path(root)
        # пропустить .git
        if git_dir in root_p.parents or root_p == git_dir:
            continue
        rel_root = root_p.relative_to(repo_root)
        # нормализуем для matcher'а (posix-style, с trailing /)
        rel_posix = str(rel_root).replace("\\", "/")
        if rel_posix == ".":
            rel_posix = ""
        else:
            rel_posix += "/"

        # фильтруем директории (чтобы не спускаться в excluded)
        kept_dirs = []
        for d in dirs:
            d_rel = (rel_posix + d + "/") if rel_posix else (d + "/")
            if mgr is not None and mgr.is_ignored(d_rel):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        for f in files:
            f_rel = (rel_posix + f) if rel_posix else f
            if mgr is not None and mgr.is_ignored(f_rel):
                continue
            # полный путь для porcelain.add
            paths.append(str(root_p / f))
    return paths


def cmd_save(message: str | None) -> None:
    if not (REPO / ".git").exists():
        cmd_init()
    name, email = _author()
    msg = message or f"backup {time.strftime('%Y-%m-%d %H:%M:%S')}"

    # собрать пути с уважением .gitignore
    paths = _collect_paths(REPO)
    print(f"staging {len(paths)} files (gitignore-respected)...")
    porcelain.add(str(REPO), paths=paths)

    try:
        sha = porcelain.commit(
            str(REPO),
            message=msg.encode("utf-8"),
            author=f"{name} <{email}>".encode("utf-8"),
            committer=f"{name} <{email}>".encode("utf-8"),
        )
        sha_s = sha.decode() if isinstance(sha, bytes) else str(sha)
        print(f"committed: {sha_s}")
        print(f"  message: {msg}")
    except Exception as e:
        if "nothing" in str(e).lower() or "no changes" in str(e).lower():
            print("nothing to commit (working tree clean)")
        else:
            raise


def cmd_status() -> None:
    if not (REPO / ".git").exists():
        print("no git repo; run 'init' first")
        return
    st = porcelain.status(str(REPO))
    # st = (staged_add, staged_del, staged_mod, unstaged_add, unstaged_del, unstaged_mod)
    labels = ["staged-add", "staged-del", "staged-mod",
              "unstaged-add", "unstaged-del", "unstaged-mod"]
    any_changes = False
    for label, group in zip(labels, st):
        if group:
            any_changes = True
            print(f"{label}:")
            for f in group:
                # dulwich возвращает bytes
                print(f"  {f.decode() if isinstance(f, bytes) else f}")
    if not any_changes:
        print("clean (no changes)")


def cmd_log(n: int = 10) -> None:
    if not (REPO / ".git").exists():
        print("no git repo")
        return
    try:
        porcelain.log(str(REPO), max_entries=n)
    except Exception as e:
        print(f"(log error: {e})")


def cmd_push(remote: str, url: str | None) -> None:
    if not (REPO / ".git").exists():
        print("no git repo; run 'init' + 'save' first")
        sys.exit(1)
    # добавить/обновить remote
    try:
        porcelain.remote_add(str(REPO), remote, url or DEFAULT_URL)
    except Exception:
        # remote уже есть — обновим config
        from dulwich.repo import Repo
        repo = Repo(str(REPO))
        cfg = repo.get_config()
        cfg.set((b"remote", remote.encode()), b"url", (url or DEFAULT_URL).encode())
        cfg.write_to_path()
    
    try:
        from dulwich.porcelain import active_branch
        ab = active_branch(str(REPO))
    except Exception:
        ab = b"master"
    
    print(f"pushing to {remote} -> {url or DEFAULT_URL} (branch: {ab.decode()}) ...")
    try:
        # dulwich push по HTTPS; если URL содержит PAT — аутентификация встроена
        refspec = b"refs/heads/" + ab
        porcelain.push(str(REPO), url or DEFAULT_URL, refspec)
        print("push OK")
    except Exception as e:
        print(f"PUSH FAILED: {e}")
        print()
        print("Возможные причины:")
        print("  1. Нет PAT в URL — используйте: https://<PAT>@github.com/NecroVIZ/pobgenerator.git")
        print("  2. default branch не 'main' — dulwich создаёт 'master' или текущий")
        print("  3. SSL/TLS проблема — dulwich использует urllib")
        sys.exit(2)


def cmd_branch() -> None:
    """Показать/создать main branch (dulwich default может быть master)."""
    if not (REPO / ".git").exists():
        print("no git repo")
        return
    from dulwich.porcelain import active_branch
    try:
        b = active_branch(str(REPO))
        print(f"active branch: {b}")
    except Exception as e:
        print(f"(branch error: {e})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Git backup via dulwich")
    ap.add_argument("cmd", choices=["init", "save", "status", "log", "push", "branch"])
    ap.add_argument("-m", "--message", default=None, help="commit message (save)")
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--url", default=None, help="remote URL (may include PAT)")
    ap.add_argument("-n", type=int, default=10, help="log entries (log)")
    args = ap.parse_args()

    if args.cmd == "init":
        cmd_init()
    elif args.cmd == "save":
        cmd_save(args.message)
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "log":
        cmd_log(args.n)
    elif args.cmd == "push":
        cmd_push(args.remote, args.url)
    elif args.cmd == "branch":
        cmd_branch()


if __name__ == "__main__":
    main()
