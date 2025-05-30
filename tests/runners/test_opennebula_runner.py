import re
from unittest.mock import patch

from alts.worker.runners import OpennebulaRunner


def runner_payload():
    """Helper method to generate common OpennebulaRunner kwargs."""
    return {
        'task_id': 'task123',
        'task_is_aborted': lambda: False,
        'dist_name': 'test-name',
        'dist_version': '1.0',
        'dist_arch': 'x86_64',
        'test_flavor': {'name': 'cloud', 'version': '1.0'},
    }


class TestOpennebulaRunner:

    @patch('alts.worker.runners.opennebula.CONFIG')
    @patch('alts.worker.runners.opennebula.pyone.OneServer')
    def test_opennebula_runner_init(self, mock_one_server, mock_config):
        mock_config.opennebula_config.username = 'testuser'
        mock_config.opennebula_config.password = 'testpass'
        mock_config.opennebula_config.rpc_endpoint = 'http://example.com/RPC2'

        runner = OpennebulaRunner(**runner_payload())

        assert runner._task_id == 'task123'
        assert runner.dist_name == 'test-name'
        assert runner.dist_version == '1.0'
        mock_one_server.assert_called_once_with(
            uri='http://example.com/RPC2', session='testuser:testpass'
        )

    @patch('alts.worker.runners.opennebula.CONFIG')
    def test_get_opennebula_template_regex(self, mock_config):
        allowed_channel_names = ['channel1', 'channel2']

        mock_config.allowed_channel_names = allowed_channel_names
        mock_config.opennebula_config.username = 'testuser'
        mock_config.opennebula_config.password = 'testpass'
        mock_config.opennebula_config.rpc_endpoint = 'http://example.com/RPC2'

        runner = OpennebulaRunner(**runner_payload())
        regex = runner.get_opennebula_template_regex()

        # It should be a Terraform-safe regex string with escaped backslashes
        assert (
            regex
            == r'test-name-1.0-(x86_64)\\.cloud-1.0\\.test_system\\.(channel1|channel2)\\.b\\d{8}-\\d+'
        )

        match = re.match(
            re.compile(regex.replace('\\\\', '\\')),  # Unescape for real use
            r'test-name-1.0-x86_64.cloud-1.0.test_system.channel2.b20251234-567',
        )
        assert match is not None

        match = re.match(
            re.compile(regex.replace('\\\\', '\\')),  # Unescape for real use
            r'test-name-1.0-aarch64.cloud-1.0.test_system.channel2.b20251234-567',
        )
        assert match is None
