import os

from abc import abstractmethod

from mako.lookup import TemplateLookup
from alts.shared.constants import X32_ARCHITECTURES
from alts.worker import CONFIG, RESOURCES_DIR
from pathlib import Path


__all__ = [
    'BaseTfRenderer',
    'DockerTfRenderer',
    'OpennebulaTfRenderer',
    'get_renderer',
]


class BaseTfRenderer():
    """
    This class describes a basic interface of terraform renderer class
    """
    TYPE = 'base'
    TEMPFILE_PREFIX = 'base_test_runner_'
    TF_VARIABLES_FILE = None
    TF_MAIN_FILE = None
    TF_VERSIONS_FILE = 'versions.tf'

    def __init__(self, workdir):
        self._work_dir = Path(workdir)
        self._class_resources_dir = os.path.join(RESOURCES_DIR, self.TYPE)
        self._template_lookup = TemplateLookup(
            directories=[RESOURCES_DIR, self._class_resources_dir]
        )


    @abstractmethod
    def render_tf_variables_file(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def render_tf_main_file(self, *args, **kwargs):
        """
        Renders main Terraform file for the instance managing

        Returns:

        """
        raise NotImplementedError

    def render_template(self, template_name, result_file_path, **kwargs):
        template = self._template_lookup.get_template(template_name)
        with open(result_file_path, 'wt') as f:
            content = template.render(**kwargs)
            f.write(content)
    

class DockerTfRenderer(BaseTfRenderer):
    """
    Helper class that renders terraform .tmpl file for DockerRunner.
    """

    TYPE = 'docker'
    TF_MAIN_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'
    ARCH_PLATFORM_MAPPING = {
        'i386': 'linux/386',
        'i486': 'linux/386',
        'i586': 'linux/386',
        'i686': 'linux/386',
        'amd64': 'linux/amd64',
        'x86_64': 'linux/amd64',
        'arm64': 'linux/arm64/v8',
        'aarch64': 'linux/arm64/v8',
        'ppc64le': 'linux/ppc64le',
        's390x': 'linux/s390x',
    }
    def __init__(self, workdir):
        super().__init__(workdir)


    def render_tf_main_file(
        self,
        dist_name,
        dist_version,
        dist_arch,
        env_name
    ):
        """
        Renders Terraform file for creating a template.

        Raises
        ------
        ValueError
            Raised if cannot map distribution architecture
            with image architecture.
        """
        docker_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        image_name = f'{dist_name}:{dist_version}'
        image_platform = self.ARCH_PLATFORM_MAPPING.get(dist_arch)
        external_network = os.environ.get('EXTERNAL_NETWORK', None)
        http_proxy = os.environ.get('http_proxy', None)
        https_proxy = os.environ.get('https_proxy', None)
        no_proxy = os.environ.get('no_proxy', None)

        self.render_template(
            f'{self.TF_MAIN_FILE}.tmpl',
            docker_tf_file,
            container_name=env_name,
            external_network=external_network,
            dist_name=dist_name,
            image_name=image_name,
            image_platform=image_platform,
            http_proxy=http_proxy,
            https_proxy=https_proxy,
            no_proxy=no_proxy,
        )


class OpennebulaTfRenderer(BaseTfRenderer):
    """
    Helper class that renders terraform .tmpl file for OpennebulaRunner.
    """
    TYPE = 'opennebula'
    TEMPFILE_PREFIX = 'opennebula_test_runner_'
    TF_VARIABLES_FILE = 'opennebula.tfvars'
    TF_MAIN_FILE = 'opennebula.tf'

    def __init__(self, workdir):
        super().__init__(workdir)

    def get_opennebula_template_regex(
            self,
            dist_name,
            dist_version,
            dist_arch,
            test_flavor_name,
            test_flavor_version
            ) -> str:
        """
        Generates regex string for Terraform to look up VM templates
        """
        channels = '|'.join(CONFIG.allowed_channel_names)
        flavor = 'base_image'
        if dist_arch == 'i686':
            arches_to_try = '|'.join(X32_ARCHITECTURES)
        else:
            arches_to_try = dist_arch
        if test_flavor_name and test_flavor_version:
            flavor = f'{test_flavor_name}-{test_flavor_version}'
        regex_str = (
            rf'{dist_name}-{dist_version}-({arches_to_try})\.{flavor}\.'
            rf'test_system\.({channels})\.b\d{{8}}-\d+'
        )
        # Escape backslashes for Terraform HCL string
        regex_terraform = regex_str.replace('\\', '\\\\')
        return regex_terraform
    
    def render_tf_main_file(
            self,
            dist_name,
            dist_version,
            dist_arch,
            vm_disk_size,
            vm_ram_size,
            vm_name,
            package_channel: str = None,
            test_flavor_name: str = None,
            test_flavor_version: str = None,
        ):
        """
        Renders Terraform file for creating a template.
        """
        nebula_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        regex_str = self.get_opennebula_template_regex(
            dist_name=dist_name,
            dist_version=dist_version,
            dist_arch=dist_arch,
            test_flavor_name=test_flavor_name,
            test_flavor_version=test_flavor_version,
        )
        self.render_template(
            template_name=f'{self.TF_MAIN_FILE}.tmpl',
            result_file_path=nebula_tf_file,
            vm_name=vm_name,
            opennebula_vm_group=CONFIG.opennebula_config.vm_group,
            channel=package_channel,
            template_regex_str=regex_str,
            vm_disk_size=vm_disk_size,
            vm_ram_size=vm_ram_size,
            opennebula_network=CONFIG.opennebula_config.network,
        )

    def render_tf_variables_file(self):
        """
        Renders Terraform file for getting variables used for a template.
        """
        vars_file = os.path.join(self._work_dir, self.TF_VARIABLES_FILE)
        self.render_template(
            f'{self.TF_VARIABLES_FILE}.tmpl',
            vars_file,
            opennebula_rpc_endpoint=CONFIG.opennebula_config.rpc_endpoint,
            opennebula_username=CONFIG.opennebula_config.username,
            opennebula_password=CONFIG.opennebula_config.password,
        )


def get_renderer(workdir, renderer_type: str = 'base') -> BaseTfRenderer:
    TF_RENDERER_MAPPING = {
        'docker': DockerTfRenderer,
        'opennebula': OpennebulaTfRenderer,
    }
    renderer_class = TF_RENDERER_MAPPING.get(renderer_type, BaseTfRenderer)
    return renderer_class(workdir)
