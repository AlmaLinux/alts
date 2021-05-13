# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-04-13

"""AlmaLinux Test System base environment runner."""

import logging
import os
import shutil
import tempfile
import time
from functools import wraps
from pathlib import Path
from typing import List, Union

import boto3
from boto3.exceptions import S3UploadFailedError
from mako.lookup import TemplateLookup
from plumbum import local

from alts.shared.exceptions import (
    InstallPackageError, ProvisionError, PublishArtifactsError,
    StartEnvironmentError, StopEnvironmentError, TerraformInitializationError,
    WorkDirPreparationError,
)
from alts.shared.types import ImmutableDict
from alts.worker import CONFIG, RESOURCES_DIR


__all__ = ['BaseRunner', 'GenericVMRunner', 'command_decorator']


def command_decorator(exception_class, artifacts_key, error_message):
    """

    Parameters
    ----------
    exception_class : class
        Specified error as exception to raise.
    artifacts_key : str
        Specified artifact's key.
    error_message : str
        Specified error message for output.

    Returns
    -------
    function
        Function decorator.
    """
    def method_wrapper(fn):
        @wraps(fn)
        def inner_wrapper(*args, **kwargs):
            self = args[0]
            args = args[1:]
            if not self._work_dir or not os.path.exists(self._work_dir):
                return
            exit_code, stdout, stderr = fn(self, *args, **kwargs)
            self._artifacts[artifacts_key] = {
                'exit_code': exit_code,
                'stderr': stderr,
                'stdout': stdout
            }
            if exit_code != 0:
                self._logger.error(f'{error_message}, exit code: {exit_code},'
                                   f' error:\n{stderr}')
                raise exception_class(error_message)
            else:
                self._logger.info('Operation completed successfully')
            # Return results of command execution
            return exit_code, stdout, stderr
        return inner_wrapper
    return method_wrapper


class BaseRunner(object):
    """
    This class describes a basic interface of test runner on some instance
    like Docker container, virtual machine, etc.

    """
    DEBIAN_FLAVORS = ('debian', 'ubuntu', 'raspbian')
    RHEL_FLAVORS = ('fedora', 'centos', 'almalinux', 'cloudlinux')
    TYPE = 'base'
    ARCHITECTURES_MAPPING = ImmutableDict(
        aarch64=['arm64', 'aarch64'],
        x86_64=['x86_64', 'amd64', 'i686'],
    )
    COST = 0
    TF_VARIABLES_FILE = None
    TF_MAIN_FILE = None
    TF_VERSIONS_FILE = 'versions.tf'
    ANSIBLE_PLAYBOOK = 'playbook.yml'
    ANSIBLE_CONFIG = 'ansible.cfg'
    ANSIBLE_INVENTORY_FILE = 'hosts'
    TEMPFILE_PREFIX = 'base_test_runner_'

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int],
                 repositories: List[dict] = None, dist_arch: str = 'x86_64'):
        """
        Base runner environment initialization.

        Parameters
        ----------
        task_id : str
            Test System task identifier.
        dist_name : str
            Distribution name.
        dist_version : str, int
            Distribution version.
        repositories : list of dict
            List of packages' repositories
        dist_arch : str
            Distribution architecture.
        """
        # Environment ID and working directory preparation
        self._task_id = task_id
        self._env_name = f'{self.TYPE}_{task_id}'
        self._logger = logging.getLogger(__file__)
        self._work_dir = None
        self._artifacts_dir = None
        self._class_resources_dir = os.path.join(RESOURCES_DIR, self.TYPE)
        self._template_lookup = TemplateLookup(
            directories=[RESOURCES_DIR, self._class_resources_dir])

        # Basic commands and tools setup
        self._ansible_connection_type = 'ssh'

        # Package-specific variables that define needed container/VM
        self._dist_name = dist_name.lower()
        self._dist_version = str(dist_version).lower()
        self._dist_arch = dist_arch.lower()

        # Package installation and test stuff
        self._repositories = repositories or []

        self._artifacts = {}

    @property
    def artifacts(self):
        """
        Gets artifacts.

        Returns
        -------
        dict
            Dictionary of artifacts.

        """
        return self._artifacts

    @property
    def pkg_manager(self):
        """
        Defines which package manager to use.

        Returns
        -------
        str
            Name of package manager.
        Raises
        ------
        ValueError
            If distribution name wasn't recognized.

        """
        if (self._dist_name == 'fedora' or self._dist_name in self.RHEL_FLAVORS
                and '8' in self._dist_version):
            return 'dnf'
        elif self._dist_name in self.RHEL_FLAVORS:
            return 'yum'
        elif self._dist_name in self.DEBIAN_FLAVORS:
            return 'apt-get'
        else:
            raise ValueError(f'Unknown distribution: {self._dist_name}')

    @property
    def ansible_connection_type(self):
        """
        Gets connection type for running ansible.

        Returns
        -------
        str
            Type of connection to establish.

        """
        return self._ansible_connection_type

    @property
    def dist_arch(self):
        """
        Gets distribution architecture.

        Returns
        -------
        str
            Distribution architecture.

        """
        return self._dist_arch

    @property
    def dist_name(self):
        """
        Gets distribution name.

        Returns
        -------
        str
            Distribution name.
        """
        return self._dist_name

    @property
    def dist_version(self):
        """
        Gets distribution version.

        Returns
        -------
        str
            Distribution version.
        """
        return self._dist_version

    @property
    def repositories(self):
        """
        Gets list of packages' repositories.

        Returns
        -------
        list
            List of packages' repositories.
        """
        return self._repositories

    @property
    def env_name(self):
        """
        Gets environment name.

        Returns
        -------
        str
            Environment name.
        """
        return self._env_name

    # TODO: Think of better implementation
    def _create_work_dir(self):
        """
        Creates temporary working directory.

        Returns
        -------
        Path
            Temporary working directory path.
        """
        if not self._work_dir or not os.path.exists(self._work_dir):
            self._work_dir = Path(tempfile.mkdtemp(prefix=self.TEMPFILE_PREFIX))
        return self._work_dir

    # TODO: Think of better implementation
    def _create_artifacts_dir(self):
        """
        Creates temporary artifacts directory.

        Returns
        -------
        Path
            Temporary artifacts directory path.
        """
        if not self._work_dir:
            self._work_dir = self._create_work_dir()
        path = self._work_dir / 'artifacts'
        if not os.path.exists(path):
            os.mkdir(path)
        return path

    def __del__(self):
        """
        Stops running environment and removes all temporary directories.
        """
        self.stop_env()
        self.erase_work_dir()

    def _render_template(self, template_name, result_file_path, **kwargs):
        """
        Renders environment's template to get configuration data.

        Parameters
        ----------
        template_name : str
            Environment's template name to render.
        result_file_path : str
            File path to write rendered environment configuration.
        kwargs : dict of str
            Additional parameters for rendering template.
        """
        template = self._template_lookup.get_template(template_name)
        with open(result_file_path, 'wt') as f:
            content = template.render(**kwargs)
            f.write(content)

    def _create_ansible_inventory_file(self, vm_ip: str = None):
        """
        Creates file with environment's configuration for ansible usage.

        Parameters
        ----------
        vm_ip : str
            Virtual machine's ip address.
        """
        inventory_file_path = os.path.join(self._work_dir,
                                           self.ANSIBLE_INVENTORY_FILE)
        self._render_template(
            f'{self.ANSIBLE_INVENTORY_FILE}.tmpl', inventory_file_path,
            env_name=self.env_name, vm_ip=vm_ip
        )

    def _render_tf_main_file(self):
        """
        Renders main Terraform file for the instance managing.

        Raises
        ------
        NotImplementedError
        """
        raise NotImplementedError

    def _render_tf_variables_file(self):
        """
        Renders Terraform variables file.

        Raises
        ------
        NotImplementedError
        """
        raise NotImplementedError

    # First step
    def prepare_work_dir_files(self, create_ansible_inventory=False):
        """
        Prepares configuration files for ansible usage in temporary
        working directory.

        Parameters
        ----------
        create_ansible_inventory : bool
            True if ansible inventory file should be created,
            False otherwise.

        Raises
        ------
        WorkDirPreparationError
            Raised if creating temporary directory with files failed.

        """
        # In case if you've removed worker folder, recreate one
        if not self._work_dir or not os.path.exists(self._work_dir):
            self._work_dir = self._create_work_dir()
            self._artifacts_dir = self._create_artifacts_dir()
        try:
            # Write resources that are not templated into working directory
            for ansible_file in (self.ANSIBLE_CONFIG, self.ANSIBLE_PLAYBOOK):
                shutil.copy(os.path.join(RESOURCES_DIR, ansible_file),
                            os.path.join(self._work_dir, ansible_file))
            shutil.copy(
                os.path.join(self._class_resources_dir, self.TF_VERSIONS_FILE),
                os.path.join(self._work_dir, self.TF_VERSIONS_FILE)
            )

            if create_ansible_inventory:
                self._create_ansible_inventory_file()
            self._render_tf_main_file()
            self._render_tf_variables_file()
        except Exception as e:
            raise WorkDirPreparationError('Cannot create working directory and'
                                          ' needed files') from e

    # After: prepare_work_dir_files
    @command_decorator(TerraformInitializationError, 'initialize_terraform',
                       'Cannot initialize terraform')
    def initialize_terraform(self):
        """
        Initializes specified Terraform environment.

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        self._logger.info(f'Initializing Terraform environment '
                          f'for {self.env_name}...')
        self._logger.debug('Running "terraform init" command')
        return local['terraform'].run('init', retcode=None, cwd=self._work_dir)

    # After: initialize_terraform
    @command_decorator(StartEnvironmentError, 'start_environment',
                       'Cannot start environment')
    def start_env(self):
        """
        Starts up initialized specified Terraform environment.

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        self._logger.info(f'Starting the environment {self.env_name}...')
        self._logger.debug('Running "terraform apply --auto-approve" command')
        cmd_args = ['apply', '--auto-approve']
        if self.TF_VARIABLES_FILE:
            cmd_args.extend(['--var-file', self.TF_VARIABLES_FILE])
        return local['terraform'].run(args=cmd_args, retcode=None,
                                      cwd=self._work_dir)

    # After: start_env
    @command_decorator(ProvisionError, 'initial_provision',
                       'Cannot run initial provision')
    def initial_provision(self, verbose=False):
        """
        Creates initial ansible provision inside specified environment.

        Parameters
        ----------
        verbose : bool
            True if additional output information is needed, False otherwise.

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        cmd_args = ['-c', self.ansible_connection_type, '-i',
                    self.ANSIBLE_INVENTORY_FILE, self.ANSIBLE_PLAYBOOK,
                    '-e', f'repositories={self._repositories}',
                    '-t', 'initial_provision']
        if verbose:
            cmd_args.append('-vvvv')
        cmd_args_str = ' '.join(cmd_args)
        self._logger.info(f'Provisioning the environment {self.env_name}...')
        self._logger.debug(
            f'Running "ansible-playbook {cmd_args_str}" command')
        return local['ansible-playbook'].run(
            args=cmd_args, retcode=None, cwd=self._work_dir)

    @command_decorator(InstallPackageError, 'install_package',
                       'Cannot install package')
    def install_package(self, package_name: str, package_version: str = None):
        """
        Installs package being tested inside testing environment.

        Parameters
        ----------
        package_name : str
            Name of a package being tested.
        package_version :
            Version of a package being tested.
        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        if package_version:
            if self.pkg_manager == 'yum':
                full_pkg_name = f'{package_name}-{package_version}'
            else:
                full_pkg_name = f'{package_name}={package_version}'
        else:
            full_pkg_name = package_name

        self._logger.info(f'Installing {full_pkg_name} on {self.env_name}...')
        cmd_args = ['-c', self.ansible_connection_type, '-i',
                    self.ANSIBLE_INVENTORY_FILE, self.ANSIBLE_PLAYBOOK,
                    '-e', f'pkg_name={full_pkg_name}',
                    '-t', 'install_package']
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            f'Running "ansible-playbook {cmd_args_str}" command')
        return local['ansible-playbook'].run(
            args=cmd_args, retcode=None, cwd=self._work_dir)

    def publish_artifacts_to_storage(self):
        """
        Uploads artifacts from temporary directory to the specified
        storage directory.

        Raises
        ------
        PublishArtifactsError
            Raised if artifacts' upload failed.
        """
        # Should upload artifacts from artifacts directory to preferred
        # artifacts storage (S3, Minio, etc.)
        for artifact_key, content in self.artifacts.items():
            log_file_path = os.path.join(self._artifacts_dir,
                                         f'{artifact_key}.log')
            with open(log_file_path, 'w+t') as f:
                f.write(f'Exit code: {content["exit_code"]}\n')
                f.write(content['stdout'])
            if content['stderr']:
                error_log_path = os.path.join(self._artifacts_dir,
                                              f'{artifact_key}_error.log')
                with open(error_log_path, 'w+t') as f:
                    f.write(content['stderr'])

        client = boto3.client(
            's3', region_name=CONFIG.s3_region,
            aws_access_key_id=CONFIG.s3_access_key_id,
            aws_secret_access_key=CONFIG.s3_secret_access_key
        )
        error_when_uploading = False
        for artifact in os.listdir(self._artifacts_dir):
            artifact_path = os.path.join(self._artifacts_dir, artifact)
            object_name = os.path.join(CONFIG.artifacts_root_directory,
                                       self._task_id, artifact)
            try:
                self._logger.info(f'Uploading artifact {artifact_path} to S3')
                client.upload_file(artifact_path, CONFIG.s3_bucket, object_name)
            except (S3UploadFailedError, ValueError) as e:
                self._logger.error(f'Cannot upload artifact {artifact_path}'
                                   f' to S3: {e}')
                error_when_uploading = True
        if error_when_uploading:
            raise PublishArtifactsError('One or more artifacts were not'
                                        ' uploaded')

    # After: install_package and run_tests
    @command_decorator(StopEnvironmentError, 'stop_environment',
                       'Cannot destroy environment')
    def stop_env(self):
        """
        Stops running testing environment.

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        if os.path.exists(self._work_dir):
            self._logger.info(f'Destroying the environment {self.env_name}...')
            self._logger.debug(
                'Running "terraform destroy --auto-approve" command')
            cmd_args = ['destroy', '--auto-approve']
            if self.TF_VARIABLES_FILE:
                cmd_args.extend(['--var-file', self.TF_VARIABLES_FILE])
            return local['terraform'].run(args=cmd_args, retcode=None,
                                          cwd=self._work_dir)

    def erase_work_dir(self):
        """
        Removes temporarily created working directories inside
        testing environment.
        """
        if self._work_dir and os.path.exists(self._work_dir):
            self._logger.info('Erasing working directory...')
            try:
                shutil.rmtree(self._work_dir)
            except Exception as e:
                self._logger.error(f'Error while erasing working directory:'
                                   f' {e}')
            else:
                self._logger.info('Working directory was successfully removed')

    def setup(self):
        """Prepares testing environment."""
        self.prepare_work_dir_files()
        self.initialize_terraform()
        self.start_env()
        self.initial_provision()

    def teardown(self, publish_artifacts: bool = True):
        """Shuts down created testing environment."""
        self.stop_env()
        if publish_artifacts:
            self.publish_artifacts_to_storage()
        self.erase_work_dir()


class GenericVMRunner(BaseRunner):

    """Generates base runner for virtual machines."""

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int],
                 repositories: List[dict] = None, dist_arch: str = 'x86_64'):
        """
        Initializes base VM's runner.

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
        ssh_key_path = os.path.abspath(
            os.path.expanduser(CONFIG.ssh_public_key_path))
        if not os.path.exists(ssh_key_path):
            self._logger.error('SSH key is missing')
        else:
            with open(ssh_key_path, 'rt') as f:
                self._ssh_public_key = f.read().strip()

    @property
    def ssh_public_key(self):
        """
        Gets ssh public key.

        Returns
        -------
        str
            Ssh public key
        """
        return self._ssh_public_key

    def _wait_for_ssh(self, retries=60):
        """
        Establishes ssh connection with a virtual machine.

        Parameters
        ----------
        retries : int
            Attempts to retry establishing ssh connection.

        Returns
        -------
        bool
            True if ssh connection was successfully establsihed,
            False otherwise.

        """
        ansible = local['ansible']
        cmd_args = ('-i', self.ANSIBLE_INVENTORY_FILE, '-m', 'ping', 'all')
        stdout = None
        stderr = None
        while retries > 0:
            exit_code, stdout, stderr = ansible.run(
                args=cmd_args, retcode=None, cwd=self._work_dir)
            if exit_code == 0:
                return True
            else:
                retries -= 1
                time.sleep(10)
        self._logger.error(f'Unable to connect to VM. '
                           f'Stdout: {stdout}\nStderr: {stderr}')
        return False

    def start_env(self):
        """
        Starts a specified testing environment using Terraform.

        Raises
        ------
        StartEnvironmentError
            Raised if a testing environment failed to start.
        """
        super().start_env()
        # VM gets its IP address only after deploy.
        # To extract it, the `vm_ip` output should be defined
        # in Terraform main file.
        exit_code, stdout, stderr = local['terraform'].run(
            args=('output', '-raw', 'vm_ip'), retcode=None, cwd=self._work_dir)
        if exit_code != 0:
            error_message = f'Cannot get VM IP: {stderr}'
            self._logger.error(error_message)
            raise StartEnvironmentError(error_message)
        self._create_ansible_inventory_file(vm_ip=stdout)
        self._logger.info('Waiting for SSH port to be available')
        is_online = self._wait_for_ssh()
        if not is_online:
            error_message = f'Machine {self.env_name} is started, but ' \
                            f'SSH connection is not working'
            self._logger.error(error_message)
            raise StartEnvironmentError(error_message)
        self._logger.info(f'Machine is available for SSH connection')
