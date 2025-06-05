import re

from unittest.mock import patch
from alts.shared.terraform import OpennebulaTfRenderer


def renderer_payload():
    """Helper method to generate common OpennebulaTfRenderer kwargs."""
    return {
        'dist_name': 'test-name',
        'dist_version': '1.0',
        'dist_arch': 'x86_64',
        'test_flavor_name': 'cloud',
        'test_flavor_version': '1.0',
    }

class TestOpenNebulaTfRenderer:
    @patch('alts.shared.terraform.CONFIG')
    def test_get_opennebula_template_regex(self, mock_config):
        allowed_channel_names = ['channel1', 'channel2']

        mock_config.allowed_channel_names = allowed_channel_names
        mock_work_dir = '/'
        renderer = OpennebulaTfRenderer(mock_work_dir)
        regex = renderer.get_opennebula_template_regex(**renderer_payload())

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
