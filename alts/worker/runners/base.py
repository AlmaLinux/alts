import fcntl
import gzip
import logging
import os
import shutil
import tempfile
import time
import typing
from functools import wraps
from pathlib import Path
from typing import List, Union

from mako.lookup import TemplateLookup
from plumbum import local

from alts.shared.exceptions import *
from alts.shared.types import ImmutableDict
from alts.shared.uploaders.base import BaseLogsUploader, UploadError
from alts.shared.uploaders.pulp import PulpLogsUploader
from alts.worker import CONFIG, RESOURCES_DIR


__all__ = ['BaseRunner', 'GenericVMRunner', 'command_decorator', 'TESTS_SECTION_NAME']


TESTS_SECTION_NAME = 'tests'
TF_INIT_LOCK_PATH = '/tmp/tf_init_lock'


def command_decorator(exception_class, artifacts_key, error_message, additional_section_name=None):
    def method_wrapper(fn):
        @wraps(fn)
        def inner_wrapper(*args, **kwargs):
            self = args[0]
            args = args[1:]
            if not self._work_dir or not os.path.exists(self._work_dir):
                return
            exit_code, stdout, stderr = fn(self, *args, **kwargs)
            add_to = self._artifacts
            if additional_section_name:
                if additional_section_name not in self._artifacts:
                    self._artifacts[additional_section_name] = {}
                    add_to = self._artifacts[additional_section_name]
            add_to[artifacts_key] = {
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
    TYPE = 'base'
    ARCHITECTURES_MAPPING = ImmutableDict(
        aarch64=['arm64', 'aarch64'],
        x86_64=['x86_64', 'amd64', 'i686', 'i386', 'i486', 'i586'],
        ppc64le=['ppc64le'],
        s390x=['s390x'],
    )
    COST = 0
    TF_VARIABLES_FILE = None
    TF_MAIN_FILE = None
    TF_VERSIONS_FILE = 'versions.tf'
    ANSIBLE_PLAYBOOK = 'playbook.yml'
    ANSIBLE_CONFIG = 'ansible.cfg'
    ANSIBLE_INVENTORY_FILE = 'hosts'
    TEMPFILE_PREFIX = 'base_test_runner_'
    INTEGRITY_TESTS_DIR = 'package_tests'

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int],
                 repositories: List[dict] = None, dist_arch: str = 'x86_64',
                 artifacts_uploader: BaseLogsUploader = None):
        # Environment ID and working directory preparation
        self._task_id = task_id
        self._env_name = f'{self.TYPE}_{task_id}'
        self._logger = logging.getLogger(__file__)
        self._work_dir = None
        self._artifacts_dir = None
        self._inventory_file_path = None
        self._integrity_tests_dir = None
        self._class_resources_dir = os.path.join(RESOURCES_DIR, self.TYPE)
        self._template_lookup = TemplateLookup(
            directories=[RESOURCES_DIR, self._class_resources_dir])
        if not artifacts_uploader:
            self._uploader = PulpLogsUploader(
                CONFIG.pulp_host, CONFIG.pulp_user, CONFIG.pulp_password,
                concurrency=CONFIG.uploader_concurrency)
        else:
            self._uploader = artifacts_uploader

        # Basic commands and tools setup
        self._ansible_connection_type = 'ssh'

        # Package-specific variables that define needed container/VM
        self._dist_name = dist_name.lower()
        self._dist_version = str(dist_version).lower()
        self._dist_arch = dist_arch.lower()

        # Package installation and test stuff
        self._repositories = repositories or []

        self._artifacts = {}
        self._uploaded_logs = None

    @property
    def artifacts(self):
        return self._artifacts

    @property
    def uploaded_logs(self) -> typing.Dict[str, str]:
        return self._uploaded_logs

    @property
    def pkg_manager(self):
        if (self._dist_name == 'fedora' or
                (self._dist_name in CONFIG.rhel_flavors
                 and self._dist_version.startswith('8'))):
            return 'dnf'
        elif self._dist_name in CONFIG.rhel_flavors:
            return 'yum'
        elif self._dist_name in CONFIG.debian_flavors:
            return 'apt-get'
        else:
            raise ValueError(f'Unknown distribution: {self._dist_name}')

    @property
    def ansible_connection_type(self):
        return self._ansible_connection_type

    @property
    def dist_arch(self):
        return self._dist_arch

    @property
    def dist_name(self):
        return self._dist_name

    @property
    def dist_version(self):
        return self._dist_version

    @property
    def repositories(self):
        return self._repositories

    @property
    def env_name(self):
        return self._env_name

    # TODO: Think of better implementation
    def _create_work_dir(self):
        if not self._work_dir or not os.path.exists(self._work_dir):
            self._work_dir = Path(tempfile.mkdtemp(prefix=self.TEMPFILE_PREFIX))
        return self._work_dir

    # TODO: Think of better implementation
    def _create_artifacts_dir(self):
        if not self._work_dir:
            self._work_dir = self._create_work_dir()
        path = self._work_dir / 'artifacts'
        if not os.path.exists(path):
            os.mkdir(path)
        return path

    def __del__(self):
        self.stop_env()
        self.erase_work_dir()

    def _render_template(self, template_name, result_file_path, **kwargs):
        template = self._template_lookup.get_template(template_name)
        with open(result_file_path, 'wt') as f:
            content = template.render(**kwargs)
            f.write(content)

    def _create_ansible_inventory_file(self, vm_ip: str = None):
        self._inventory_file_path = os.path.join(
            self._work_dir, self.ANSIBLE_INVENTORY_FILE)
        self._render_template(
            f'{self.ANSIBLE_INVENTORY_FILE}.tmpl', self._inventory_file_path,
            env_name=self.env_name, vm_ip=vm_ip,
            connection_type=self.ansible_connection_type
        )

    def _render_tf_main_file(self):
        """
        Renders main Terraform file for the instance managing

        Returns:

        """
        raise NotImplementedError

    def _render_tf_variables_file(self):
        """
        Renders Terraform variables file

        Returns:

        """
        raise NotImplementedError

    # First step
    def prepare_work_dir_files(self, create_ansible_inventory=False):
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
            # Copy integrity tests into working directory
            self._integrity_tests_dir = os.path.join(
                self._work_dir, self.INTEGRITY_TESTS_DIR)
            shutil.copytree(os.path.join(RESOURCES_DIR,
                                         self.INTEGRITY_TESTS_DIR),
                            self._integrity_tests_dir)

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
        self._logger.info(f'Initializing Terraform environment '
                          f'for {self.env_name}...')
        self._logger.debug('Running "terraform init" command')
        lock = None
        try:
            lock = open(TF_INIT_LOCK_PATH, 'a+')
            lock_fileno = lock.fileno()
            while True:
                try:
                    fcntl.flock(lock_fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    time.sleep(1)
                else:
                    break
            return local['terraform'].run('init', retcode=None,
                                          cwd=self._work_dir)
        finally:
            if lock:
                fcntl.flock(lock_fileno, fcntl.LOCK_UN)
                lock.close()

    # After: initialize_terraform
    @command_decorator(StartEnvironmentError, 'start_environment',
                       'Cannot start environment')
    def start_env(self):
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
        # To pass dictionary into Ansible variables we need to pass
        # variables itself as a dictionary thus doing this weird
        # temporary dictionary
        var_dict = {'repositories': self._repositories,
                    'integrity_tests_dir': self._integrity_tests_dir}
        cmd_args = ['-i', self.ANSIBLE_INVENTORY_FILE, self.ANSIBLE_PLAYBOOK,
                    '-e', f'{var_dict}', '-t', 'initial_provision']
        self._logger.info(f'Command args: {cmd_args}')
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
    def install_package(self, package_name: str, package_version: str = None,
                        module_name: str = None, module_stream: str = None,
                        module_version: str = None):
        if package_version:
            if self.pkg_manager in ('yum', 'dnf'):
                full_pkg_name = f'{package_name}-{package_version}'
            else:
                full_pkg_name = f'{package_name}={package_version}'
        else:
            full_pkg_name = package_name

        self._logger.info(f'Installing {full_pkg_name} on {self.env_name}...')
        cmd_args = ['-i', self.ANSIBLE_INVENTORY_FILE, self.ANSIBLE_PLAYBOOK,
                    '-e', f'pkg_name={full_pkg_name}']
        if module_name and module_stream and module_version:
            cmd_args.extend(['-e', f'module_name={module_name}',
                             '-e', f'module_stream={module_stream}',
                             '-e', f'module_version={module_version}'])
        cmd_args.extend(['-t', 'install_package'])
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            f'Running "ansible-playbook {cmd_args_str}" command')
        return local['ansible-playbook'].run(
            args=cmd_args, retcode=None, cwd=self._work_dir)

    @command_decorator(PackageIntegrityTestsError, 'package_integrity_tests',
                       'Package integrity tests failed',
                       additional_section_name=TESTS_SECTION_NAME)
    def run_package_integrity_tests(self, package_name: str,
                                    package_version: str = None):
        """
        Run basic integrity tests for the package

        Parameters
        ----------
        package_name:       str
            Package name
        package_version:    str
            Package version

        Returns
        -------
        tuple
            Exit code, stdout and stderr from executed command

        """
        cmd_args = ['--tap-stream', '--tap-files', '--tap-outdir',
                    self._artifacts_dir, '--hosts', 'ansible://all',
                    '--ansible-inventory', self._inventory_file_path,
                    '--package-name', package_name]
        if package_version:
            full_pkg_name = f'{package_name}-{package_version}'
            cmd_args.extend(['--package-version', package_version])
        else:
            full_pkg_name = package_name
        cmd_args.append('tests')
        self._logger.info('Running package integrity tests for '
                          '%s on %s...', full_pkg_name, self.env_name)
        return local['py.test'].run(args=cmd_args, retcode=None,
                                    cwd=self._integrity_tests_dir)

    def publish_artifacts_to_storage(self):
        # Should upload artifacts from artifacts directory to preferred
        # artifacts storage (S3, Minio, etc.)

        def write_to_file(file_base_name: str, artifacts_section: dict):
            log_file_path = os.path.join(
                self._artifacts_dir, f'{file_base_name}_{self._task_id}.log')
            with open(log_file_path, 'wb') as fd:
                content = (
                    f'Exit code: {artifacts_section["exit_code"]}\n'
                    f'Stdout:\n\n{artifacts_section["stdout"]}'
                )
                if artifacts_section.get('stderr'):
                    content += f'Stderr:\n\n{artifacts_section["stderr"]}'
                fd.write(gzip.compress(content.encode()))

        for artifact_key, content in self.artifacts.items():
            if artifact_key == TESTS_SECTION_NAME:
                for inner_artifact_key, inner_content in content.items():
                    log_base_name = f'{TESTS_SECTION_NAME}_{inner_artifact_key}'
                    write_to_file(log_base_name, inner_content)

            else:
                write_to_file(artifact_key, content)

        upload_dir = os.path.join(CONFIG.artifacts_root_directory,
                                  self._task_id)
        try:
            artifacts = self._uploader.upload(
                self._artifacts_dir, upload_dir=upload_dir)
            self._uploaded_logs = artifacts
        except UploadError as e:
            raise PublishArtifactsError from e

    # After: install_package and run_tests
    @command_decorator(StopEnvironmentError, 'stop_environment',
                       'Cannot destroy environment')
    def stop_env(self):
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
        self.prepare_work_dir_files()
        self.initialize_terraform()
        self.start_env()
        self.initial_provision()

    def teardown(self, publish_artifacts: bool = True):
        self.stop_env()
        if publish_artifacts:
            try:
                self.publish_artifacts_to_storage()
            except Exception as e:
                self._logger.exception('Exception while publishing artifacts: '
                                       '%s', str(e))
        self.erase_work_dir()


class GenericVMRunner(BaseRunner):

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int],
                 repositories: List[dict] = None, dist_arch: str = 'x86_64'):
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
        return self._ssh_public_key

    def _wait_for_ssh(self, retries=60):
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
        self._logger.info('Waiting for SSH port to be available')
        is_online = self._wait_for_ssh()
        if not is_online:
            error_message = f'Machine {self.env_name} is started, but ' \
                            f'SSH connection is not working'
            self._logger.error(error_message)
            raise StartEnvironmentError(error_message)
        self._logger.info(f'Machine is available for SSH connection')
