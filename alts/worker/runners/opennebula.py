# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System opennebula environment runner."""

import os
import re
from typing import Callable, List, Optional, Union

import pyone

from alts.shared.constants import X32_ARCHITECTURES
from alts.shared.exceptions import VMImageNotFound
from alts.worker import CONFIG
from alts.worker.runners.base import GenericVMRunner

__all__ = ['OpennebulaRunner']


class OpennebulaRunner(GenericVMRunner):
    """Opennebula environment runner for testing tasks."""

    TYPE = 'opennebula'
    TEMPFILE_PREFIX = 'opennebula_test_runner_'
    TF_VARIABLES_FILE = 'opennebula.tfvars'
    TF_MAIN_FILE = 'opennebula.tf'

    def __init__(
        self,
        task_id: str,
        task_is_aborted: Callable,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        package_channel: Optional[str] = None,
        test_configuration: Optional[dict] = None,
        verbose: bool = False,
    ):
        super().__init__(
            task_id,
            task_is_aborted,
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            test_configuration=test_configuration,
            verbose=verbose,
        )
        self.package_channel = package_channel
        user = CONFIG.opennebula_config.username
        password = CONFIG.opennebula_config.password
        self.opennebula_client = pyone.OneServer(
            uri=CONFIG.opennebula_config.rpc_endpoint,
            session=f'{user}:{password}',
        )

    def find_template_and_image_ids(
        self,
    ) -> tuple[Optional[int], Optional[int]]:
        platform_name_version = f'{self.dist_name}-{self.dist_version}'
        templates = self.opennebula_client.templatepool.info(-1, -1, -1, -1)
        regex_str = (
            r'(?P<platform_name>\w+(-\w+)?)-(?P<version>\d+.\d+)'
            r'-(?P<arch>\w+).*.test_system'
        )
        if self.package_channel is not None:
            channels = '|'.join(CONFIG.allowed_channel_names)
            regex_str += f'.({channels})'
        # Filter images to leave only those that are related to the particular
        # platform
        # Note: newer OS don't have 32-bit images usually, so we need to map
        # them to correct 64-bit
        filter_arch = self.dist_arch
        if (self.dist_name in CONFIG.rhel_flavors
                and self.dist_version.startswith(('8', '9', '10'))
                and self.dist_arch in X32_ARCHITECTURES):
            filter_arch = 'x86_64'
        elif self.dist_arch == 'i686':
            filter_arch = 'i386'
        filtered_templates = []
        for template in templates.VMTEMPLATE:
            conditions = [
                bool(re.search(regex_str, template.NAME)),
                template.NAME.startswith(platform_name_version),
                filter_arch in template.NAME,
            ]
            if self.package_channel is not None:
                conditions.append(self.package_channel in template.NAME)
            if all(conditions):
                filtered_templates.append(template)
        template_params = (
            f'distribution: {self.dist_name}, '
            f'dist version: {self.dist_version}, '
            f'architecture: {self.dist_arch}'
        )
        if not filtered_templates:
            if self.package_channel is not None:
                template_params += f' channel: {self.package_channel}'
            raise VMImageNotFound(
                'Cannot find a template '
                f'with the parameters: {template_params}'
            )
        # Sort templates in order to get the latest image as first in the list
        sorted_templates = sorted(
            filtered_templates,
            key=lambda i: i.NAME,
            reverse=True,
        )
        if not sorted_templates:
            return None, None
        final_template = sorted_templates[0]
        final_disk = final_template.TEMPLATE.get('DISK', {})
        final_image_name = final_disk.get('IMAGE')
        final_image_id = final_disk.get('IMAGE_ID')
        final_template_id = final_template.ID
        final_template_name = final_template.NAME
        if final_image_id:
            return final_template.ID, int(final_image_id)
        images_pool = self.opennebula_client.imagepool.info(-2, -1, -1, -1)
        images = [
            image
            for image in images_pool.IMAGE
            if image.NAME == final_image_name
        ]
        if images:
            final_image_id = images[0].ID
        self._logger.info(
            'We found template "%s" with ID "%s" '
            'and image "%s" with ID "%s" for params: "%s"',
            final_template_name,
            final_template_id,
            final_image_name,
            final_image_id,
            template_params,
        )
        return final_template_id, final_image_id

    def _render_tf_main_file(self):
        """
        Renders Terraform file for creating a template.
        """
        nebula_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        template_id, image_id = self.find_template_and_image_ids()
        self._render_template(
            template_name=f'{self.TF_MAIN_FILE}.tmpl',
            result_file_path=nebula_tf_file,
            vm_name=self.env_name,
            opennebula_vm_group=CONFIG.opennebula_config.vm_group,
            image_id=image_id,
            template_id=template_id,
            vm_disk_size=self.vm_disk_size,
            vm_ram_size=self.vm_ram_size,
            opennebula_network=CONFIG.opennebula_config.network,
        )

    def _render_tf_variables_file(self):
        """
        Renders Terraform file for getting variables used for a template.
        """
        vars_file = os.path.join(self._work_dir, self.TF_VARIABLES_FILE)
        self._render_template(
            f'{self.TF_VARIABLES_FILE}.tmpl',
            vars_file,
            opennebula_rpc_endpoint=CONFIG.opennebula_config.rpc_endpoint,
            opennebula_username=CONFIG.opennebula_config.username,
            opennebula_password=CONFIG.opennebula_config.password,
        )
