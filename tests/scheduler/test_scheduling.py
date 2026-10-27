import pytest
from pydantic import ValidationError

from alts.scheduler import CONFIG
from alts.scheduler import scheduling
from alts.shared.models import QueueArchOverride, TaskRequestPayload
from alts.worker.runners import DockerRunner

get_queue_arch = scheduling.TestsScheduler.get_queue_arch


def make_payload(dist_name: str, dist_version, dist_arch: str):
    return TaskRequestPayload(
        bs_task_id=1,
        runner_type='docker',
        dist_name=dist_name,
        dist_version=dist_version,
        dist_arch=dist_arch,
        package_name='bash',
    )


class TestGetQueueArch:
    @pytest.mark.parametrize(
        'dist_name, dist_version, dist_arch, expected',
        [
            pytest.param(
                'almalinux', '10', 'x86_64', 'x86_64_v3',
                id='major-version',
            ),
            pytest.param(
                'almalinux', '10.1', 'x86_64', 'x86_64_v3',
                id='full-version',
            ),
            pytest.param(
                'almalinux', 10, 'x86_64', 'x86_64_v3',
                id='int-version',
            ),
            pytest.param(
                'almalinux', '10', 'i686', 'x86_64_v3',
                id='mapped-arch',
            ),
            pytest.param(
                'almalinux', '10', 'x86_64_v2', 'x86_64_v2',
                id='other-arch',
            ),
            pytest.param(
                'almalinux', '9', 'x86_64', 'x86_64',
                id='other-version',
            ),
            pytest.param(
                'centos', '10', 'x86_64', 'x86_64',
                id='other-dist',
            ),
            pytest.param(
                'almalinux', '10', 'aarch64', 'aarch64',
                id='no-override-for-arch',
            ),
        ],
    )
    def test_get_queue_arch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dist_name: str,
        dist_version,
        dist_arch: str,
        expected: str,
    ):
        monkeypatch.setattr(
            CONFIG,
            'queue_arch_overrides',
            [
                QueueArchOverride(
                    dist_name='almalinux',
                    dist_versions=['10'],
                    arch='x86_64',
                    queue_arch='x86_64_v3',
                ),
            ],
        )
        payload = make_payload(dist_name, dist_version, dist_arch)
        assert get_queue_arch(DockerRunner, payload) == expected

    def test_no_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(CONFIG, 'queue_arch_overrides', [])
        payload = make_payload('almalinux', '10', 'x86_64')
        assert get_queue_arch(DockerRunner, payload) == 'x86_64'

    def test_unknown_arch(self):
        payload = make_payload('almalinux', '10', 'riscv64')
        assert get_queue_arch(DockerRunner, payload) is None

    def test_undeclared_queue_arch_rejected(self):
        with pytest.raises(ValidationError):
            QueueArchOverride(
                dist_name='almalinux',
                dist_versions=['10'],
                arch='x86_64',
                queue_arch='x86-64-v3',
            )
