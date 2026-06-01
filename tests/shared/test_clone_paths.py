"""Path-collision regression tests for QA-repo cloning.

When two repositories share a basename (e.g. gerrit's `QA` and gitlab's
`QA.git`), the old code would land both at `<dir>/QA`, so a checkout of
one upstream would silently satisfy the reuse guard for the other and a
branch from the second upstream would fail to check out.

These tests pin the host-aware layout so the collision can't come back.
"""
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alts.shared.utils import git_utils
from alts.shared.utils.git_utils import (
    clone_git_repo,
    repo_reference_subpath,
)


GERRIT_QA = 'ssh://gerrit.cloudlinux.com:29418/QA'
GITLAB_QA = 'git@gitlab.corp.cloudlinux.com:clos/ci-tools/QA.git'


@pytest.fixture
def fake_git(monkeypatch):
    """Replace ``plumbum.local['git']`` with a recording stub.

    Returns the stub so tests can inspect the args the clone was invoked
    with. The stub also marks the cloned directory as existing so the
    subsequent ``checkout`` call doesn't blow up on a missing path.
    """
    # `clone_args` captures the `git clone ...` invocation specifically;
    # `__clone_git_repo` uses `local['git']` for the clone and `checkout`
    # later uses `local['bash']`, and the fake serves the same runner for
    # both, so we must not just keep "the last call".
    recorded = {'clone_args': None}
    git_runner = MagicMock()

    def run(args, retcode=None, timeout=None):
        # `args[2]` is the destination path with the new layout
        # (`['clone', url, dest, '--depth', '1', ...]`).
        if len(args) >= 3 and args[0] == 'clone':
            recorded['clone_args'] = list(args)
            Path(args[2]).mkdir(parents=True, exist_ok=True)
        return 0, '', ''

    git_runner.run.side_effect = run
    git_runner.with_cwd.return_value = git_runner

    fake_local = MagicMock()
    fake_local.__getitem__.return_value = git_runner

    monkeypatch.setattr(git_utils, 'local', fake_local)
    # `checkout` shells out via the same `local`; the bash stub just no-ops.
    return recorded


class TestCloneDestinationIsHostAware:
    def test_gerrit_qa_lands_under_gerrit_host_dir(
        self, tmp_path, fake_git,
    ):
        result = clone_git_repo(
            GERRIT_QA, 'master', tmp_path, logging.getLogger('test'),
        )
        # `repo_reference_subpath` strips trailing `.git` for the on-disk path.
        expected = tmp_path / 'gerrit.cloudlinux.com' / 'QA'
        assert result == expected
        # `git clone <url> <dest> --depth 1` — third positional arg is dest.
        assert fake_git['clone_args'][0:3] == ['clone', GERRIT_QA, str(expected)]
        assert expected.exists()

    def test_gitlab_qa_lands_under_gitlab_path_dir(
        self, tmp_path, fake_git,
    ):
        result = clone_git_repo(
            GITLAB_QA, 'master', tmp_path, logging.getLogger('test'),
        )
        expected = (
            tmp_path
            / 'gitlab.corp.cloudlinux.com'
            / 'clos'
            / 'ci-tools'
            / 'QA'
        )
        assert result == expected
        assert fake_git['clone_args'][0:3] == ['clone', GITLAB_QA, str(expected)]
        assert expected.exists()

    def test_gerrit_and_gitlab_qa_do_not_collide(
        self, tmp_path, fake_git,
    ):
        gerrit_path = clone_git_repo(
            GERRIT_QA, 'master', tmp_path, logging.getLogger('test'),
        )
        gitlab_path = clone_git_repo(
            GITLAB_QA, 'master', tmp_path, logging.getLogger('test'),
        )
        assert gerrit_path != gitlab_path
        # Neither path should be a prefix of the other.
        assert not str(gitlab_path).startswith(f'{gerrit_path}/')
        assert not str(gerrit_path).startswith(f'{gitlab_path}/')

    def test_parent_directories_are_created(self, tmp_path, fake_git):
        # Cleanly missing intermediate dirs (`gitlab.corp.../clos/ci-tools`)
        # must be created before `git clone` runs, otherwise the subprocess
        # would error out.
        clone_git_repo(
            GITLAB_QA, 'master', tmp_path, logging.getLogger('test'),
        )
        assert (
            tmp_path
            / 'gitlab.corp.cloudlinux.com'
            / 'clos'
            / 'ci-tools'
        ).is_dir()


class TestRepoReferenceSubpathMatchesCloneLayout:
    """Guards the contract the runners rely on: the on-disk layout
    used by ``__clone_git_repo`` and the layout used to derive
    ``remote_workdir`` / VM repo paths are the *same* subpath, modulo
    the trailing ``.git`` which is stripped for the working tree.
    """

    @pytest.mark.parametrize(
        'url',
        [GERRIT_QA, GITLAB_QA],
    )
    def test_subpath_minus_git_suffix_is_used_for_checkout_dir(self, url):
        subpath = repo_reference_subpath(url)
        assert subpath.endswith('.git')
        working_tree_subpath = subpath[:-4]
        # The host segment is always present so two different upstreams
        # with the same basename always diverge at this segment.
        assert '/' in working_tree_subpath
        host, _, _ = working_tree_subpath.partition('/')
        assert host  # non-empty host segment
