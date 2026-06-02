"""Tests for the third-party-repo SSH host config model.

`ThirdPartyRepoSshHost` entries are seeded into a test VM's
`/root/.ssh/config` so QA repos on gerrit/gitlab can be cloned. The
`user` field is optional and must be omitted from the serialized form
when unset (the Ansible template keys off `user is defined`).
"""
import pytest

from alts.shared.models import (
    CachedTestRepo,
    CeleryConfig,
    ThirdPartyRepoSshHost,
)


class TestThirdPartyRepoSshHost:
    def test_host_only_entry_omits_user_when_serialized(self):
        entry = ThirdPartyRepoSshHost(host='gitlab.corp.cloudlinux.com')
        dumped = entry.model_dump(exclude_none=True)
        assert dumped == {'host': 'gitlab.corp.cloudlinux.com'}
        # `user` must NOT be present, otherwise the Jinja `is defined`
        # guard would emit an empty `User` line.
        assert 'user' not in dumped

    def test_host_with_user_keeps_user_when_serialized(self):
        entry = ThirdPartyRepoSshHost(
            host='gerrit.cloudlinux.com',
            user='alternatives',
        )
        dumped = entry.model_dump(exclude_none=True)
        assert dumped == {
            'host': 'gerrit.cloudlinux.com',
            'user': 'alternatives',
        }

    def test_host_is_required(self):
        with pytest.raises(Exception):
            ThirdPartyRepoSshHost()


class TestCachedTestRepo:
    def test_requires_src_and_dest(self):
        entry = CachedTestRepo(
            src='/opt/QA',
            dest='/opt/gerrit.cloudlinux.com/QA',
        )
        assert entry.model_dump() == {
            'src': '/opt/QA',
            'dest': '/opt/gerrit.cloudlinux.com/QA',
        }

    @pytest.mark.parametrize('kwargs', [{}, {'src': '/opt/QA'}])
    def test_missing_field_raises(self, kwargs):
        with pytest.raises(Exception):
            CachedTestRepo(**kwargs)


class TestCeleryConfigDefault:
    @pytest.mark.parametrize(
        'field_name',
        ['third_party_repo_ssh_hosts', 'cached_test_repos'],
    )
    def test_field_defaults_to_empty(self, field_name):
        # No built-in entries: an unconfigured deployment is a no-op
        # (the role tasks are gated on non-empty lists).
        field = CeleryConfig.model_fields[field_name]
        default = field.get_default(call_default_factory=True)
        assert default == []
