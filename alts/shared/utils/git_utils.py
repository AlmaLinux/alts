from logging import Logger
from pathlib import Path
from random import randint
from time import sleep
from typing import Optional

from filelock import FileLock
from plumbum import local

from alts.shared.utils.path_utils import get_abspath


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
    git_repo_path = Path(
        work_dir,
        Path(repo_url).name.replace('.git', ''),
    )
    if git_repo_path.exists():
        return git_repo_path
    file_lock_path = f'/tmp/alts_git_lock_{git_repo_path.name}'
    logger.debug('Cloning the git repo: %s', repo_url)
    args = ['clone', repo_url, '--depth', '1']
    if reference_directory:
        args.extend(
            ['--reference-if-able', get_abspath(reference_directory)]
        )
    last_error = ''
    for attempt in range(1, 6):
        with FileLock(file_lock_path):
            exit_code, _, stderr = local['git'].with_cwd(work_dir).run(
                args,
                retcode=None,
                timeout=cmd_timeout,
            )
        if exit_code == 0:
            return git_repo_path
        logger.warning(
            'Cannot clone the git repo: %s\n%s',
            repo_url,
            stderr,
        )
        last_error = stderr
        sleep_time = randint(1, 10)
        logger.info('Retrying in %d seconds', sleep_time)
        sleep(sleep_time)
    logger.error(
        'Unable to clone the git repo %s:\n%s',
        repo_url, last_error,
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
