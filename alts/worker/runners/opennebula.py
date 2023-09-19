# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System opennebula environment runner."""

import os
import re
import typing

import pyone

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
        self, task_id: str, dist_name: str,
        dist_version: typing.Union[str, int],
        repositories: typing.List[dict] = None,
        dist_arch: str = 'x86_64',
        package_channel: typing.Optional[str] = None,
        test_configuration: typing.Optional[dict] = None,
    ):
        super().__init__(
            task_id,
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            test_configuration=test_configuration
        )
        self.package_channel = package_channel

    def find_image(self) -> pyone.bindings.IMAGESub:
        user = CONFIG.opennebula_username
        password = CONFIG.opennebula_password
        platform_name_version = f'{self.dist_name}-{self.dist_version}'
        nebula = pyone.OneServer(
            CONFIG.opennebula_rpc_endpoint,
            session=f'{user}:{password}'
        )
        # Get all images visible to the Opennebula user
        images = nebula.imagepool.info(-1, -1, -1, -1)
        regex_str = (r'(?P<platform_name>\w+(-\w+)?)-(?P<version>\d+.\d+)'
                     r'-(?P<arch>\w+).*.test_system')
        if self.package_channel is not None:
            channels = '|'.join(CONFIG.allowed_channel_names)
            regex_str += f'.({channels})'
        # Filter images to leave only those that are related to the particular
        # platform
        filtered_images = []
        for image in images.IMAGE:
            conditions = [
                bool(re.search(regex_str, image.NAME)),
                image.NAME.startswith(platform_name_version),
                self.dist_arch in image.NAME,
            ]
            if self.package_channel is not None:
                conditions.append(self.package_channel in image.NAME)
            if all(conditions):
                filtered_images.append(image)
        if not filtered_images:
            image_params = (
                f'distribution: {self.dist_name}, '
                f'dist version: {self.dist_version}, '
                f'architecture: {self.dist_arch}'
            )
            if self.package_channel is not None:
                image_params += f' channel: {self.package_channel}'
            raise VMImageNotFound(
                f'Cannot find the image with the parameters: {image_params}')
        # Sort images in order to get the latest image as first in the list
        sorted_images = sorted(filtered_images, key=lambda i: i.NAME,
                               reverse=True)
        return sorted_images[0]

    def _render_tf_main_file(self):
        """
        Renders Terraform file for creating a template.
        """
        vm_group_name = CONFIG.opennebula_vm_group
        nebula_tf_file = self._work_dir.joinpath(self.TF_MAIN_FILE)
        self._render_template(
            template_name=f'{self.TF_MAIN_FILE}.tmpl',
            result_file_path=nebula_tf_file,
            vm_name=self.env_name,
            vm_group_name=vm_group_name,
            image_id=self.find_image(),
        )

    def _render_tf_variables_file(self):
        """
        Renders Terraform file for getting variables used for a template.
        """
        vars_file = self._work_dir.joinpath(self.TF_VARIABLES_FILE)
        self._render_template(
            f'{self.TF_VARIABLES_FILE}.tmpl', vars_file,
            opennebula_rpc_endpoint=CONFIG.opennebula_rpc_endpoint,
            opennebula_username=CONFIG.opennebula_username,
            opennebula_password=CONFIG.opennebula_password
        )
