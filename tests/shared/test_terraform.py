import pytest
import re

from alts.shared.terraform import DockerTfRenderer, OpennebulaTfRenderer

class TestOpenNebulaTfRenderer:
    def test_get_opennebula_template_regex(
        self,
        opennebula_tf_renderer_payload,
        patched_opennebula_config,
    ):
        channels_string = "|".join(patched_opennebula_config.allowed_channel_names)
        renderer = OpennebulaTfRenderer('/tmp')
        regex = renderer.get_opennebula_template_regex(
            dist_name=opennebula_tf_renderer_payload["dist_name"],
            dist_version=opennebula_tf_renderer_payload["dist_version"],
            dist_arch=opennebula_tf_renderer_payload["dist_arch"],
            test_flavor_name=opennebula_tf_renderer_payload["test_flavor_name"],
            test_flavor_version=opennebula_tf_renderer_payload["test_flavor_version"],
        )

        # It should be a Terraform-safe regex string with escaped backslashes
        unescaped_expected_regex = (
            rf'^{opennebula_tf_renderer_payload["dist_name"]}-{opennebula_tf_renderer_payload["dist_version"]}-'
            rf'({opennebula_tf_renderer_payload["dist_arch"]})\.{opennebula_tf_renderer_payload["test_flavor_name"]}-?'
            rf'{opennebula_tf_renderer_payload["test_flavor_version"]}\.test_system\.'
            rf'({channels_string})\.b\d{{8}}-\d+'
        )
        expected_escaped = unescaped_expected_regex.replace('\\', '\\\\')
        assert regex == expected_escaped

        # Check that valid image name will be matched
        assert re.match(
            unescaped_expected_regex,
            (
                f'{opennebula_tf_renderer_payload["dist_name"]}-{opennebula_tf_renderer_payload["dist_version"]}'
                f'-{opennebula_tf_renderer_payload["dist_arch"]}.{opennebula_tf_renderer_payload["test_flavor_name"]}'
                f'-{opennebula_tf_renderer_payload["test_flavor_version"]}.test_system.'
                f'{patched_opennebula_config.allowed_channel_names[0]}.b20250605-123'
            )
        )
        # Image name with a different architecture won't be matched
        assert not re.match(
            unescaped_expected_regex,
            (
                f'{opennebula_tf_renderer_payload["dist_name"]}-{opennebula_tf_renderer_payload["dist_version"]}'
                f'-aarch64.{opennebula_tf_renderer_payload["test_flavor_name"]}'
                f'-{opennebula_tf_renderer_payload["test_flavor_version"]}.test_system.'
                f'{patched_opennebula_config.allowed_channel_names[0]}.b20250605-123'
            )
        )

    def test_render_tf_main_file(
        self,
        tmp_path,
        opennebula_tf_renderer_payload,
        patched_opennebula_config,
    ):
        channels_string = "|".join(patched_opennebula_config.allowed_channel_names)
        renderer = OpennebulaTfRenderer(tmp_path)
        renderer.render_tf_main_file(
            dist_name=opennebula_tf_renderer_payload["dist_name"],
            dist_version=opennebula_tf_renderer_payload["dist_version"],
            dist_arch=opennebula_tf_renderer_payload["dist_arch"],
            vm_disk_size=opennebula_tf_renderer_payload["vm_disk_size"],
            vm_ram_size=opennebula_tf_renderer_payload["vm_ram_size"],
            vm_name=opennebula_tf_renderer_payload["vm_name"],
            package_channel=opennebula_tf_renderer_payload["package_channel"],
            test_flavor_name=opennebula_tf_renderer_payload["test_flavor_name"],
            test_flavor_version=opennebula_tf_renderer_payload["test_flavor_version"],
        )
        tf_file = tmp_path / renderer.TF_MAIN_FILE
        assert tf_file.exists(), f"{tf_file} was not created"
        content = tf_file.read_text()

        # Check regex for VM template search
        regex_base = (
            rf'^{opennebula_tf_renderer_payload["dist_name"]}-{opennebula_tf_renderer_payload["dist_version"]}-'
            rf'({opennebula_tf_renderer_payload["dist_arch"]})\.{opennebula_tf_renderer_payload["test_flavor_name"]}-?'
            rf'{opennebula_tf_renderer_payload["test_flavor_version"]}\.test_system\.'
            rf'({channels_string})\.b\d{{8}}-\d+'
        )
        regex_escaped = regex_base.replace('\\', '\\\\')
        assert regex_escaped in content

        # Check VM propertires
        assert f'resource "opennebula_virtual_machine" "{opennebula_tf_renderer_payload["vm_name"]}"' in content
        assert f'name = "{opennebula_tf_renderer_payload["vm_name"]}"' in content
        assert f'group = "{patched_opennebula_config.opennebula_config.vm_group}"' in content
        assert f'memory = "{opennebula_tf_renderer_payload["vm_ram_size"]}"' in content

        # Check output
        assert f'opennebula_virtual_machine.{opennebula_tf_renderer_payload["vm_name"]}.ip' in content

    def test_render_tf_variables_file(
        self,
        tmp_path,
        patched_opennebula_config,
    ):
        renderer = OpennebulaTfRenderer(tmp_path)
        renderer.render_tf_variables_file()
        vars_file = tmp_path / renderer.TF_VARIABLES_FILE
        assert vars_file.exists()
        content = vars_file.read_text()

        assert 'http://localhost:2633/RPC2' in content
        assert 'testuser' in content
        assert 'testpass' in content


class TestDockerTfRenderer:
    TEST_CONTAINER_NAME = 'test_container'
    TEST_DIST_NAME = 'test_dist'
    TEST_DIST_VERSION = '1.0'

    @pytest.mark.parametrize(
        "arch,expected_platform",
        [
            ("x86_64", "linux/amd64"),
            ("i386", "linux/386"),
            ("aarch64", "linux/arm64/v8"),
        ]
    )
    def test_render_tf_main_file(self, tmp_path, arch, expected_platform):
        renderer = DockerTfRenderer(tmp_path)

        renderer.render_tf_main_file(
            dist_name=self.TEST_DIST_NAME,
            dist_version=self.TEST_DIST_VERSION,
            dist_arch=arch,
            env_name=self.TEST_CONTAINER_NAME,
        )

        tf_file = tmp_path / renderer.TF_MAIN_FILE
        assert tf_file.exists(), f"{tf_file} was not created"

        content = tf_file.read_text()

        # check docker_container
        assert re.search(rf'resource\s+"docker_container"\s+"{self.TEST_CONTAINER_NAME}"', content)
        assert re.search(rf'name\s*=\s*"{self.TEST_CONTAINER_NAME}"', content)
        assert re.search(rf'image\s*=\s*docker_image\.{self.TEST_DIST_NAME}\.image_id', content)

        # check docker_image
        assert re.search(rf'resource\s+"docker_image"\s+"{self.TEST_DIST_NAME}"', content)
        assert re.search(rf'name\s*=\s*"{self.TEST_DIST_NAME}:{self.TEST_DIST_VERSION}"', content)
        assert re.search(rf'platform\s*=\s*"{expected_platform}"', content)
