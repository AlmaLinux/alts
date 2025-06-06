import pytest
from unittest.mock import patch


@pytest.fixture
def patched_opennebula_config():
    with patch('alts.shared.terraform.CONFIG') as mock_config:
        mock_config.opennebula_config.vm_group = 'test-group'
        mock_config.opennebula_config.network = 'test-net'
        mock_config.opennebula_config.rpc_endpoint = 'http://localhost:2633/RPC2'
        mock_config.opennebula_config.username = 'testuser'
        mock_config.opennebula_config.password = 'testpass'
        mock_config.allowed_channel_names = ['channel1', 'channel2']
        yield mock_config

@pytest.fixture
def opennebula_tf_renderer_payload() -> dict:
    return {
        "dist_name": "test-name",
        "dist_version": "1.0",
        "dist_arch": "x86_64",
        "vm_name": 4096,
        "vm_disk_size": 4096,
        "vm_ram_size": 20480,
        "package_channel": "channel2",
        "test_flavor_name": "cloud",
        "test_flavor_version": "1.0",
    }
