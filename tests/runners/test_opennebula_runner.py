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

    @patch('alts.worker.runners.base.CONFIG')
    @patch('alts.worker.runners.opennebula.CONFIG')
    @patch('alts.worker.runners.opennebula.pyone.OneServer')
    def test_opennebula_runner_init(self, mock_one_server, mock_config, mock_base_config):
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

