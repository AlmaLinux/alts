# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System opennebula environment runner."""

import os
import re
import time
from typing import (
    Callable, Dict,
    List,
    Optional,
    Union,
)

import pyone
from plumbum import local

from alts.shared.constants import X32_ARCHITECTURES
from alts.shared.exceptions import (
    OpennebulaVMStopError,
    VMImageNotFound,
)
from alts.shared.uploaders.base import BaseLogsUploader
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
        artifacts_uploader: Optional[BaseLogsUploader] = None,
        package_channel: Optional[str] = None,
        test_configuration: Optional[dict] = None,
        test_flavor: Optional[Dict[str, str]] = None,
        vm_alive: bool = False,
        verbose: bool = True,
    ):
        super().__init__(
            task_id,
            task_is_aborted,
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            artifacts_uploader=artifacts_uploader,
            package_channel=package_channel,
            test_configuration=test_configuration,
            test_flavor=test_flavor,
            vm_alive=vm_alive,
            verbose=verbose,
        )
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
        channels = '|'.join(CONFIG.allowed_channel_names)
        regex_str = r'(?P<platform_name>\w+(-\w+)?)-(?P<version>\d+(.\d+)?)-(?P<arch>\w+)'
        if self.test_flavor:
            name = self.test_flavor['name']
            version = self.test_flavor['version']
            regex_str += f'.(?P<flavor_name>{name})-(?P<flavor_version>{version})'
        regex_str += f'.base_image.test_system.({channels}).b\d+' # noqa
        # Filter images to leave only those that are related to the particular
        # platform
        # Note: newer OS don't have 32-bit images usually, so we need to try
        # to find correct 64-bit replacement
        if self.dist_arch == 'i686':
            arches_to_try = X32_ARCHITECTURES
        else:
            arches_to_try = [self.dist_arch]

        def search_template(include_channel: bool = True):
            f_templates = []
            for arch in arches_to_try:
                for template in templates.VMTEMPLATE:
                    conditions = [
                        bool(re.search(regex_str, template.NAME)),
                        template.NAME.startswith(platform_name_version),
                        arch in template.NAME,
                    ]
                    if self.package_channel is not None and include_channel:
                        conditions.append(self.package_channel in template.NAME)
                    if all(conditions):
                        f_templates.append(template)
                        break
            return f_templates

        filtered_templates = search_template()
        self._logger.info(
            'Filtered templates: %s',
            [i.NAME for i in filtered_templates],
        )
        template_params = (
            f'distribution: {self.dist_name}, '
            f'dist version: {self.dist_version}, '
            f'architecture: {self.dist_arch}'
        )
        if not filtered_templates:
            self._logger.info('Searching new templates without the channel')
            if self.package_channel is not None and self.package_channel == 'beta':
                filtered_templates = search_template(include_channel=False)
                self._logger.info(
                    'Filtered templates: %s',
                    [i.NAME for i in filtered_templates],
                )
                template_params += f' channel: {self.package_channel}'
                if not filtered_templates:
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

    def destroy_vm_via_api(self, vm_id: int):
        def vm_info():
            return self.opennebula_client.vm.info(vm_id)

        def wait_for_state(state: pyone.VM_STATE, attempts: int = 120):
            info = vm_info()
            while info.STATE != state and attempts > 0:
                self._logger.info('VM state: %s', info.STATE)
                time.sleep(5)
                attempts -= 1
                info = vm_info()
            if info.STATE != state:
                raise OpennebulaVMStopError(
                    f'State {state} is not achieved, actual state: {info.STATE}'
                )

        def recover_delete():
            # 3 stands for 'delete'
            try:
                self.opennebula_client.vm.recover(vm_id, 3)
                wait_for_state(pyone.VM_STATE.DONE, attempts=60)
            except:
                self._logger.exception(
                    'Cannot terminate VM %s via API, please contact infra '
                    'team to ask for help', vm_id
                )

        try:
            self.opennebula_client.vm.action('terminate-hard', vm_id)
            wait_for_state(pyone.VM_STATE.DONE)
        except OpennebulaVMStopError:
            self._logger.warning(
                'Cannot delete VM with terminate-hard, trying recover-delete'
            )
            recover_delete()
        except Exception as e:
            self._logger.error(
                'Unexpected error during execution of '
                'terminate-hard on VM %s:\n%s',
                vm_id, str(e)
            )
            recover_delete()

    def _stop_env(self):
        if self._vm_alive:
            return 0, "WARNING: VM won't be destroyed because vm_alive=True was given", ""
        stop_exit_code, stop_out, stop_err = super()._stop_env()
        if stop_exit_code == 0:
            return stop_exit_code, stop_out, stop_err

        self._logger.warning(
            'Cannot stop VM conventionally. Output:\n%s\nStderr:\n%s',
            stop_out, stop_err
        )
        id_exit_code, vm_id, id_stderr = local['terraform'].with_env(TF_LOG='TRACE').with_cwd(
            self._work_dir).run(
            args=('output', '-raw', '-no-color', 'vm_id'),
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )
        self._logger.debug('VM ID: %s', vm_id)
        if id_exit_code != 0 or not vm_id:
            self._logger.warning('Cannot get VM ID: %s', id_stderr)
            return id_exit_code, 'Cannot get VM ID', id_stderr
        self.destroy_vm_via_api(int(vm_id.strip()))
        return 0, f'{vm_id} is destroyed via API', ''
