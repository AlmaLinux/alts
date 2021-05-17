# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System opennebula environment runner."""

import os

from alts.worker import CONFIG
from alts.worker.runners.base import GenericVMRunner


__all__ = ['OpennebulaRunner']


class OpennebulaRunner(GenericVMRunner):

    """Opennebula environment runner for testing tasks."""

    TYPE = 'opennebula'
    TEMPFILE_PREFIX = 'opennebula_test_runner_'
    TF_VARIABLES_FILE = 'opennebula.tfvars'
    TF_MAIN_FILE = 'opennebula.tf'

    def _render_tf_main_file(self):
        """
        Renders Terraform file for creating a template.
        """
        template_id = CONFIG.get_opennebula_template_id(
            self.dist_name, self.dist_version, self.dist_arch)
        vm_group_name = CONFIG.opennebula_vm_group
        nebula_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        self._render_template(
            f'{self.TF_MAIN_FILE}.tmpl', nebula_tf_file,
            template_id=template_id, vm_name=self.env_name,
            vm_group_name=vm_group_name, ssh_public_key=self.ssh_public_key
        )

    def _render_tf_variables_file(self):
        """
        Renders Terraform file for getting variables used for a template.
        """
        vars_file = os.path.join(self._work_dir, self.TF_VARIABLES_FILE)
        self._render_template(
            f'{self.TF_VARIABLES_FILE}.tmpl', vars_file,
            opennebula_rpc_endpoint=CONFIG.opennebula_rpc_endpoint,
            opennebula_username=CONFIG.opennebula_username,
            opennebula_password=CONFIG.opennebula_password
        )
