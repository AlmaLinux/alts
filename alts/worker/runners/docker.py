# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System docker environment runner."""

import os
import shutil
from typing import Union, List

from plumbum import local

from alts.shared.exceptions import ProvisionError
from alts.worker import CONFIG, RESOURCES_DIR
from alts.worker.runners.base import BaseRunner


__all__ = ['DockerRunner']


class DockerRunner(BaseRunner):

    """Docker environment runner for testing tasks."""

    TYPE = 'docker'
    TF_MAIN_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'
    X32_ARCHES = ('i686', 'i586', 'i386')
    ARCH_MAPPING = {
        'i686': 'i386',
        'i586': 'i386',
        'i386': 'i386',
    }
    DOCKER_RUN_SCRIPT = 'run.sh'

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int],
                 repositories: List[dict] = None, dist_arch: str = 'x86_64'):
        """
        Docker environment class initialization.

        Parameters
        ----------
        task_id : str
            Test System task identifier.
        dist_name : str
            Distribution name.
        dist_version : str, int
            Distribution version.
        repositories : list of dict
            List of packages' repositories/
        dist_arch : str
            Distribution architecture.
        """
        super().__init__(task_id, dist_name, dist_version,
                         repositories=repositories, dist_arch=dist_arch)
        self._ansible_connection_type = 'docker'

    def _render_tf_main_file(self):
        """
        Renders Terraform file for creating a template.

        Raises
        ------
        ValueError
            Raised if cannot map distribution architecture
            with image architecture.
        """
        docker_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        if self.dist_arch in self.X32_ARCHES:
            image_arch = self.ARCH_MAPPING.get(self.dist_arch)
            if not image_arch:
                raise ValueError(
                    f'Cannot get image for architecture {self.dist_arch}')
            image_name = f'{image_arch}/{self.dist_name}:{self.dist_version}'
        else:
            image_name = f'{self.dist_name}:{self.dist_version}'

        self._render_template(
            f'{self.TF_MAIN_FILE}.tmpl', docker_tf_file,
            dist_name=self.dist_name, image_name=image_name,
            container_name=self.env_name, work_dir=self._work_dir
        )

    def _render_tf_variables_file(self):
        pass

    def prepare_work_dir_files(self, create_ansible_inventory=True):
        """
        Prepares configuration files in temporary working directory.

        Parameters
        ----------
        create_ansible_inventory : bool
            True if ansible inventory file should be created,
            False otherwise.
        """
        super().prepare_work_dir_files(
            create_ansible_inventory=create_ansible_inventory)
        run_script_path = os.path.join(self._work_dir, self.DOCKER_RUN_SCRIPT)
        shutil.copy(os.path.join(RESOURCES_DIR, self.TYPE, self.DOCKER_RUN_SCRIPT),
                    run_script_path)
        os.chmod(run_script_path, 755)

    def _exec(self, cmd_with_args: ()):
        """
        Executes a command inside docker container.

        Parameters
        ----------
        cmd_with_args : tuple
            Arguments to create a command and its arguments to execute
            inside the container.

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        cmd = ('exec', str(self.env_name), *cmd_with_args)
        cmd_str = ' '.join(cmd)
        self._logger.debug(f'Running "docker {cmd_str}" command')
        return local['docker'].run(args=cmd, retcode=None, cwd=self._work_dir)

    def initial_provision(self, verbose=False):
        """
        Creates initial provision inside docker container.

        Parameters
        ----------
        verbose : bool
            True if additional output information is needed, False otherwise.

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        Raises
        ------
        ProvisionError
            Raised if error occurred with package manager updating
            or installing.
        """
        # Installing python3 package before running Ansible
        if self._dist_name in CONFIG.debian_flavors:
            self._logger.info('Installing python3 package...')
            exit_code, stdout, stderr = self._exec(
                (self.pkg_manager, 'update'))
            if exit_code != 0:
                raise ProvisionError(f'Cannot update metadata: {stderr}')
            cmd_args = (self.pkg_manager, 'install', '-y', 'python3')
            exit_code, stdout, stderr = self._exec(cmd_args)
            if exit_code != 0:
                raise ProvisionError(f'Cannot install package python3: '
                                     f'{stderr}')
            self._logger.info('Installation is completed')
        return super().initial_provision(verbose=verbose)
