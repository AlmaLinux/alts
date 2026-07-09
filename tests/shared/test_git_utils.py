"""Tests for alts.shared.utils.git_utils."""
import pytest

from alts.shared.utils.git_utils import (
    prepare_gerrit_command,
    repo_reference_subpath,
)


class TestRepoReferenceSubpath:
    @pytest.mark.parametrize(
        'url,expected',
        [
            (
                'ssh://gerrit.cloudlinux.com:29418/QA',
                'gerrit.cloudlinux.com/QA.git',
            ),
            (
                'https://gitlab.cloudlinux.com/qa-team/QA.git',
                'gitlab.cloudlinux.com/qa-team/QA.git',
            ),
            (
                'https://github.com/foo/bar.git',
                'github.com/foo/bar.git',
            ),
            (
                'git@gitlab.com:qa/QA.git',
                'gitlab.com/qa/QA.git',
            ),
            (
                'git@gitlab.com:qa/QA',
                'gitlab.com/qa/QA.git',
            ),
            # SCP-style URL without a user prefix.
            (
                'gitlab.com:qa/QA.git',
                'gitlab.com/qa/QA.git',
            ),
            (
                'gerrit.cloudlinux.com:QA',
                'gerrit.cloudlinux.com/QA.git',
            ),
        ],
    )
    def test_known_urls_map_to_host_aware_subpaths(self, url, expected):
        assert repo_reference_subpath(url) == expected

    def test_same_basename_different_hosts_diverge(self):
        gerrit = repo_reference_subpath(
            'ssh://gerrit.cloudlinux.com:29418/QA',
        )
        gitlab = repo_reference_subpath(
            'https://gitlab.cloudlinux.com/qa-team/QA.git',
        )
        assert gerrit != gitlab
        # Both keys preserve enough host information to be distinguishable.
        assert 'gerrit.cloudlinux.com' in gerrit
        assert 'gitlab.cloudlinux.com' in gitlab

    def test_host_is_lowercased(self):
        assert repo_reference_subpath(
            'https://GITLAB.COM/foo/Bar.git',
        ) == 'gitlab.com/foo/Bar.git'

    def test_unparseable_url_falls_back_to_basename(self):
        # No scheme, no SCP-style colon — degenerate input.
        assert repo_reference_subpath('just-a-name') == 'just-a-name.git'


class TestPrepareGerritCommand:
    CLEANUP = 'git reset --hard && git clean -fdx'

    def test_master_cleans_before_checkout(self):
        cmd = prepare_gerrit_command('master')
        assert cmd.startswith(self.CLEANUP)
        assert cmd == f'{self.CLEANUP} && git checkout master && git pull'

    def test_named_branch_cleans_before_checkout(self):
        cmd = prepare_gerrit_command('some-feature-branch')
        assert cmd.startswith(self.CLEANUP)
        assert 'git checkout some-feature-branch' in cmd

    def test_changeset_cleans_before_checkout(self):
        # git_ref "review/patchset" -> refs/changes/<last 2 of review>/...
        cmd = prepare_gerrit_command('256840/2')
        assert cmd.startswith(self.CLEANUP)
        assert "git fetch origin 'refs/changes/40/256840/2'" in cmd
        assert cmd.endswith('git checkout FETCH_HEAD')

    def test_incomplete_changeset_returns_empty(self):
        # A ref with an empty segment isn't a valid review/patchset pair.
        assert prepare_gerrit_command('256840/') == ''
