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
    OpenNebulaQuotaExceededError,
    OpennebulaVMStopError,
    VMImageNotFound,
)
from alts.shared.uploaders.base import BaseLogsUploader
from alts.worker import CONFIG
from alts.worker.quota_cache import OpenNebulaQuotaCache, QuotaInfo
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

    def find_template_and_image_ids(
        self,
    ) -> tuple[Optional[int], Optional[int]]:
        platform_name_version = f'{self.dist_name}-{self.dist_version}'
        templates = self.opennebula_client.templatepool.info(-1, -1, -1, -1)
        channels = '|'.join(CONFIG.allowed_channel_names)
        regex_str = r'(?P<platform_name>\w+(-\w+)?)-(?P<version>\d+(\.\d+)?)-(?P<arch>\w+)'
        flavor = 'base_image'
        if self.test_flavor:
            name = self.test_flavor['name']
            version = self.test_flavor['version']
            flavor = f'(?P<flavor_name>{name})-(?P<flavor_version>{version})'
        regex_str += f'\.{flavor}\.test_system\.({channels})\.b\d{{8}}-\d+'
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
                    self._template_not_found = True
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

        template_data = final_template.TEMPLATE
        required_cpu = float(template_data.get('CPU', 1))
        required_memory = int(template_data.get('MEMORY', self.vm_ram_size))
        disk_size = final_disk.get('SIZE')
        required_disk = int(disk_size) if disk_size else self.vm_disk_size

        self._logger.debug(
            'Template %s requires: CPU=%.1f, Memory=%dMB, Disk=%dMB',
            final_template_name, required_cpu, required_memory, required_disk
        )

        if final_image_id:
            self.check_quota_capacity(required_cpu, required_memory, required_disk)
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
        self.check_quota_capacity(required_cpu, required_memory, required_disk)
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

    def _get_group_id_by_name(self, group_name: str) -> Optional[int]:
        """
        Look up OpenNebula group ID by group name.

        Parameters
        ----------
        group_name : str
            Name of the OpenNebula group.

        Returns
        -------
        Optional[int]
            Group ID if found, None otherwise.
        """
        try:
            group_pool = self.opennebula_client.grouppool.info()
            for group in group_pool.GROUP:
                if group.NAME == group_name:
                    return group.ID
        except Exception as e:
            self._logger.error('Failed to fetch group pool: %s', e)
        return None

    def check_quota_capacity(
        self,
        required_cpu: float,
        required_memory: int,
        required_disk: int,
    ):
        """
        Check if OpenNebula group has sufficient quota capacity.

        Parameters
        ----------
        required_cpu : float
            Required CPU cores for the VM.
        required_memory : int
            Required memory in MB for the VM.
        required_disk : int
            Required disk size in MB for the VM.

        Raises
        ------
        OpenNebulaQuotaExceededError
            If quota capacity is insufficient for creating a new VM.
        """
        if not CONFIG.opennebula_config.quota_check_enabled:
            return

        group_name = CONFIG.opennebula_config.vm_group
        if not group_name:
            self._logger.warning(
                'Quota check enabled but vm_group not configured'
            )
            return

        group_id = self._get_group_id_by_name(group_name)
        if group_id is None:
            self._logger.warning(
                'Could not find group ID for group name: %s', group_name
            )
            return

        cache = OpenNebulaQuotaCache(CONFIG.opennebula_config.quota_cache_ttl)
        quota = cache.get(group_id)

        if quota is None:
            quota = self._fetch_quota_from_api(group_id)
            cache.set(group_id, quota)

        if not self._has_sufficient_capacity(
            quota, required_cpu, required_memory, required_disk
        ):
            raise OpenNebulaQuotaExceededError(
                f'Insufficient OpenNebula quota capacity for group {group_name}: '
                f'Required CPU={required_cpu}, Memory={required_memory}MB, '
                f'Disk={required_disk}MB. '
                f'Current: VMs {quota.vms_used}/{quota.vms_limit}, '
                f'CPU {quota.cpu_used}/{quota.cpu_limit}, '
                f'Memory {quota.memory_used}/{quota.memory_limit} MB, '
                f'Disk {quota.disk_used}/{quota.disk_limit} MB'
            )

        self._logger.info(
            'Quota check passed for group %s: Required CPU=%.1f, Memory=%dMB, '
            'Disk=%dMB. Current: VMs %d/%d, CPU %.1f/%.1f, '
            'Memory %d/%d MB, Disk %d/%d MB',
            group_name,
            required_cpu, required_memory, required_disk,
            quota.vms_used, quota.vms_limit,
            quota.cpu_used, quota.cpu_limit,
            quota.memory_used, quota.memory_limit,
            quota.disk_used, quota.disk_limit
        )

    def _fetch_quota_from_api(self, group_id: int) -> QuotaInfo:
        """
        Fetch quota information from OpenNebula API.

        Parameters
        ----------
        group_id : int
            OpenNebula group ID.

        Returns
        -------
        QuotaInfo
            Quota usage and limits information.
        """
        self._logger.debug('Fetching quota from API for group %d', group_id)
        group_info = self.opennebula_client.group.info(group_id)

        vm_quota = group_info.VM_QUOTA.VM if hasattr(group_info, 'VM_QUOTA') else None

        if vm_quota is None:
            self._logger.warning(
                'No VM_QUOTA found for group %d, assuming unlimited', group_id
            )
            return QuotaInfo(
                vms_used=0,
                vms_limit=-1,
                cpu_used=0.0,
                cpu_limit=-1.0,
                memory_used=0,
                memory_limit=-1,
                disk_used=0,
                disk_limit=-1,
                timestamp=0.0,
            )

        def parse_quota_value(value, default=0):
            if value is None or value == '' or value == '-1':
                return -1
            try:
                return int(value)
            except (ValueError, TypeError):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return default

        return QuotaInfo(
            vms_used=parse_quota_value(vm_quota.VMS_USED, 0),
            vms_limit=parse_quota_value(vm_quota.VMS, -1),
            cpu_used=float(parse_quota_value(vm_quota.CPU_USED, 0)),
            cpu_limit=float(parse_quota_value(vm_quota.CPU, -1)),
            memory_used=parse_quota_value(vm_quota.MEMORY_USED, 0),
            memory_limit=parse_quota_value(vm_quota.MEMORY, -1),
            disk_used=parse_quota_value(vm_quota.SYSTEM_DISK_SIZE_USED, 0),
            disk_limit=parse_quota_value(vm_quota.SYSTEM_DISK_SIZE, -1),
            timestamp=0.0,
        )

    def _has_sufficient_capacity(
        self,
        quota: QuotaInfo,
        required_cpu: float,
        required_memory: int,
        required_disk: int,
    ) -> bool:
        """
        Check if quota has sufficient capacity for a new VM.

        Parameters
        ----------
        quota : QuotaInfo
            Current quota usage and limits.
        required_cpu : float
            Required CPU cores for the VM.
        required_memory : int
            Required memory in MB for the VM.
        required_disk : int
            Required disk size in MB for the VM.

        Returns
        -------
        bool
            True if sufficient capacity available.
        """
        margin = CONFIG.opennebula_config.quota_safety_margin

        def check_limit(used, limit, required=1):
            if limit == -1:
                return True
            effective_limit = limit * (1 - margin)
            return used + required <= effective_limit

        checks = [
            ('VMs', check_limit(quota.vms_used, quota.vms_limit, 1)),
            ('CPU', check_limit(quota.cpu_used, quota.cpu_limit, required_cpu)),
            ('Memory', check_limit(quota.memory_used, quota.memory_limit, required_memory)),
            ('Disk', check_limit(quota.disk_used, quota.disk_limit, required_disk)),
        ]

        for resource, passed in checks:
            if not passed:
                self._logger.warning(
                    'Quota check failed for %s', resource
                )
                return False

        return True

    def find_vm_by_name(self, vm_name: str) -> Optional[int]:
        """
        Find VM ID by VM name using OpenNebula API.

        Parameters
        ----------
        vm_name : str
            Name of the VM to search for.

        Returns
        -------
        Optional[int]
            VM ID if found, None otherwise.
        """
        try:
            vmpool = self.opennebula_client.vmpool.info(-1, -1, -1, -1)
            for vm in vmpool.VM:
                if vm.NAME == vm_name:
                    self._logger.info('Found VM "%s" with ID %d', vm_name, vm.ID)
                    return vm.ID
            self._logger.warning('VM with name "%s" not found in vmpool', vm_name)
        except Exception as e:
            self._logger.error('Failed to search for VM by name "%s": %s', vm_name, e)
        return None

    def destroy_vm_by_name(self, vm_name: str) -> bool:
        """
        Destroy VM by name. This method finds the VM by name and then destroys it.

        Note: OpenNebula API requires VM ID for deletion, so this method must
        query for the ID first before deletion.

        Parameters
        ----------
        vm_name : str
            Name of the VM to destroy.

        Returns
        -------
        bool
            True if VM was found and destroyed, False otherwise.
        """
        vm_id = self.find_vm_by_name(vm_name)
        if vm_id is None:
            return False
        self.destroy_vm_via_api(vm_id)
        return True

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
            self._logger.warning(
                'Start environment step failed, but VM may have been '
                'partially created. Attempting cleanup.',
            )
        elif self.vm_alive:
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
            self._logger.warning(
                'Cannot get VM ID: %s. Attempting to destroy VM by name',
                id_stderr
            )
            if self.destroy_vm_by_name(self.env_name):
                return 0, f'VM "{self.env_name}" is destroyed via API (found by name)', ''
            return id_exit_code, 'Cannot get VM ID and VM not found by name', id_stderr
        try:
            parsed_vm_id = int(vm_id.strip())
        except ValueError:
            # Terraform may return warning text (e.g. no outputs in state)
            # instead of an integer VM id on partially initialized envs.
            self._logger.warning(
                'Unexpected VM ID output: %s. Attempting to destroy VM by name',
                vm_id
            )
            if self.destroy_vm_by_name(self.env_name):
                return 0, f'VM "{self.env_name}" is destroyed via API (found by name)', ''
            return 1, 'Cannot parse VM ID and VM not found by name', str(vm_id)
        self.destroy_vm_via_api(parsed_vm_id)
        return 0, f'{parsed_vm_id} is destroyed via API', ''
