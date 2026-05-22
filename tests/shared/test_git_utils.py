"""Tests for alts.shared.utils.git_utils.repo_reference_subpath."""
import pytest

from alts.shared.utils.git_utils import repo_reference_subpath


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
