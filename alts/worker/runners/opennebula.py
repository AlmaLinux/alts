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
        verbose: bool = False,
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
        self._template_not_found = False

    def get_opennebula_template_regex(self) -> str:
        """
        Generates regex string for Terraform to look up VM templates
        """
        channels = '|'.join(CONFIG.allowed_channel_names)
        flavor = 'base_image'
        if self.dist_arch == 'i686':
            arches_to_try = '|'.join(X32_ARCHITECTURES)
        else:
            arches_to_try = self.dist_arch
        if self.test_flavor:
            name = self.test_flavor['name']
            version = self.test_flavor['version']
            flavor = f'{name}-{version}'
        regex_str = (
            rf'{self.dist_name}-{self.dist_version}-({arches_to_try})\.{flavor}\.'
            rf'test_system\.({channels})\.b\d{{8}}-\d+'
        )
        # Escape backslashes for Terraform HCL string
        regex_terraform = regex_str.replace('\\', '\\\\')
        return regex_terraform

    def _render_tf_main_file(self):
        """
        Renders Terraform file for creating a template.
        """
        nebula_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        regex_str = self.get_opennebula_template_regex()
        self._render_template(
            template_name=f'{self.TF_MAIN_FILE}.tmpl',
            result_file_path=nebula_tf_file,
            vm_name=self.env_name,
            opennebula_vm_group=CONFIG.opennebula_config.vm_group,
            channel=(
                self.package_channel if self.package_channel is not None else ''
            ),
            template_regex_str=regex_str,
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
        if self._template_not_found:
            err_msg = (
                'VM is not created because template was not found'
            )
            self._logger.warning(err_msg)
            return 0, err_msg, ''
        if self.start_env_failed:
            err_msg = (
                'VM is not created because start environment step failed'
            )
            self._logger.warning(err_msg)
            return 0, err_msg, ''
        if self.vm_alive:
            return 0, "WARNING: VM won't be destroyed because vm_alive=True was given", ""
        stop_exit_code, stop_out, stop_err = super()._stop_env()
        if stop_exit_code == 0:
            return stop_exit_code, stop_out, stop_err

        self._logger.warning(
            'Cannot stop VM conventionally. Output:\n%s\nStderr:\n%s',
            stop_out, stop_err
        )
        id_exit_code, vm_id, id_stderr = local['terraform'].with_cwd(
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
