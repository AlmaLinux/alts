import os
import re
import urllib.parse
from logging import Logger
from pathlib import Path
from random import randint
from time import sleep
from typing import Optional

from plumbum import local

from alts.shared.utils.path_utils import get_abspath


_SCP_URL_RE = re.compile(r'^(?P<user>[^@]+@)?(?P<host>[^:/]+):(?P<path>.+)$')


def repo_reference_subpath(repo_url: str) -> str:
    """
    Derive a host-aware on-disk subpath for a git repository's reference
    mirror. Two repositories that share a basename but live on different
    hosts (e.g. a Gerrit "QA" and a GitLab "QA") must map to different
    subpaths to avoid clobbering each other's bare mirrors.

    Examples
    --------
    >>> repo_reference_subpath('ssh://gerrit.cloudlinux.com:29418/QA')
    'gerrit.cloudlinux.com/QA.git'
    >>> repo_reference_subpath('https://gitlab.cloudlinux.com/qa/QA.git')
    'gitlab.cloudlinux.com/qa/QA.git'
    >>> repo_reference_subpath('git@gitlab.com:qa/QA.git')
    'gitlab.com/qa/QA.git'
    """
    host = None
    path = None
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.hostname:
        host = parsed.hostname
        path = parsed.path
    else:
        scp_match = _SCP_URL_RE.match(repo_url)
        if scp_match:
            host = scp_match.group('host')
            path = scp_match.group('path')
    if not host or not path:
        # Unparsable URL — fall back to basename so behaviour stays
        # predictable; collisions across hosts remain the caller's problem
        # in that degenerate case.
        basename = os.path.basename(repo_url) or 'repo'
        return basename if basename.endswith('.git') else f'{basename}.git'
    path = path.strip('/')
    if not path.endswith('.git'):
        path = f'{path}.git'
    return f'{host.lower()}/{path}'


def checkout(
    git_ref: str,
    git_repo_path: Path,
    logger: Logger,
    cmd_timeout: int = 300,
):
    logger.debug('Switching to the git branch/tag: %s', git_ref)
    exit_code, _, stderr = local['bash'].with_cwd(git_repo_path).run(
        ['-c', f'git fetch origin && git checkout {git_ref}'],
        retcode=None,
        timeout=cmd_timeout,
    )


def git_reset_hard(git_repo_path: Path, logger: Logger):
    exit_code, _, stderr = local['bash'].with_cwd(git_repo_path).run(
        ['-c', 'git checkout master && git reset --hard origin/master'],
        retcode=None,
    )
    if exit_code != 0:
        logger.error(
            'Cannot reset the git index and working tree:\n%s',
            stderr,
        )


def __clone_git_repo(
    repo_url: str,
    work_dir: Path,
    logger: Logger,
    reference_directory: Optional[str] = None,
    cmd_timeout: int = 300,
):
    subpath = repo_reference_subpath(repo_url)
    if subpath.endswith('.git'):
        subpath = subpath[:-4]
    git_repo_path = Path(work_dir, subpath)
    if git_repo_path.exists():
        return git_repo_path
    git_repo_path.parent.mkdir(parents=True, exist_ok=True)
    logger.debug('Cloning the git repo: %s', repo_url)
    args = ['clone', repo_url, str(git_repo_path), '--depth', '1']
    if reference_directory:
        args.extend(
            ['--reference-if-able', get_abspath(reference_directory)]
        )
    exit_code, stdout, stderr = local['git'].run(
        args,
        retcode=None,
        timeout=cmd_timeout,
    )
    if exit_code == 0:
        return git_repo_path
    logger.error(
        'Unable to clone the git repo %s:\n%s\n\n%s',
        repo_url, stdout, stderr,
    )
    return


def clone_git_repo(
    repo_url: str,
    git_ref: str,
    work_dir: Path,
    logger: Logger,
    reference_directory: Optional[str] = None,
    cmd_timeout: int = 300,
) -> Optional[Path]:
    git_repo_path = __clone_git_repo(
        repo_url,
        work_dir,
        logger,
        reference_directory=reference_directory,
        cmd_timeout=cmd_timeout,
    )
    if git_repo_path:
        checkout(git_ref, git_repo_path, logger)
    return git_repo_path


def prepare_gerrit_command(git_ref: str) -> str:
    command = ''
    if git_ref == 'master':
        command = 'git checkout master && git pull'
    elif '/' not in git_ref and not git_ref.isdigit():
        command = (
            f'git reset --hard origin/{git_ref} && '
            f'git checkout {git_ref} && git pull'
        )
    elif all(git_ref.split('/')):
        review, patchset = git_ref.split('/')
        sm = review[-2:]
        command = (
            'git checkout master && git pull && '
            f"git fetch origin 'refs/changes/{sm}/{review}/{patchset}' "
            '--force --update-head-ok --progress && '
            'git checkout FETCH_HEAD'
        )
    return command


def clone_gerrit_repo(
    repo_url: str,
    git_ref: str,
    work_dir: Path,
    logger: Logger,
    reference_directory: Optional[str] = None,
    cmd_timeout: int = 300,
) -> Optional[Path]:
    # ssh://gerrit.test.com:00000/repo
    git_repo_path = __clone_git_repo(
        repo_url,
        work_dir,
        logger,
        reference_directory=reference_directory,
        cmd_timeout=cmd_timeout,
    )
    if git_repo_path:
        gerrit_command = prepare_gerrit_command(git_ref)
        if not gerrit_command:
            logger.debug('Nothing to do, skipping')
            return
        exit_code, _, stderr = local['bash'].with_cwd(git_repo_path).run(
            ['-c', gerrit_command],
            retcode=None,
            timeout=cmd_timeout,
        )
        if exit_code != 0:
            logger.error(
                'Cannot execute gerrit command: %s\n%s',
                gerrit_command,
                stderr,
            )
            return
    return git_repo_path
