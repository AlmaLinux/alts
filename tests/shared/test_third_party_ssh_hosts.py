"""Tests for the third-party-repo SSH host config model.

`ThirdPartyRepoSshHost` entries are seeded into a test VM's
`/root/.ssh/config` so QA repos on gerrit/gitlab can be cloned. The
`user` field is optional and must be omitted from the serialized form
when unset (the Ansible template keys off `user is defined`).
"""
import pytest

from alts.shared.models import CeleryConfig, ThirdPartyRepoSshHost


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


class TestCeleryConfigDefault:
    def test_third_party_repo_ssh_hosts_defaults_to_empty(self):
        # No built-in hosts: an unconfigured deployment writes no
        # ~/.ssh/config (the role task is gated on a non-empty list).
        config = CeleryConfig.__new__(CeleryConfig)
        field = CeleryConfig.model_fields['third_party_repo_ssh_hosts']
        # Pydantic stores the default factory / default value.
        default = field.get_default(call_default_factory=True)
        assert default == []
