import datetime
import fcntl
import gzip
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mako.lookup import TemplateLookup
from plumbum import local

from alts.shared.exceptions import (
    InstallPackageError,
    PackageIntegrityTestsError,
    ProvisionError,
    PublishArtifactsError,
    StartEnvironmentError,
    StopEnvironmentError,
    TerraformInitializationError,
    ThirdPartyTestError,
    UninstallPackageError,
    WorkDirPreparationError,
)
from alts.shared.types import ImmutableDict
from alts.shared.uploaders.base import BaseLogsUploader, UploadError
from alts.shared.uploaders.pulp import PulpLogsUploader
from alts.worker import CONFIG, RESOURCES_DIR
from alts.worker.executors.ansible import AnsibleExecutor
from alts.worker.executors.bats import BatsExecutor
from alts.worker.executors.shell import ShellExecutor

__all__ = [
    'BaseRunner',
    'GenericVMRunner',
    'command_decorator',
    'TESTS_SECTION_NAME',
    'TESTS_SECTIONS_NAMES',
]


TESTS_SECTION_NAME = 'tests'
THIRD_PARTY_SECTION_NAME = 'third_party'
TESTS_SECTIONS_NAMES = (
    TESTS_SECTION_NAME,
    THIRD_PARTY_SECTION_NAME,
)
TF_INIT_LOCK_PATH = '/tmp/tf_init_lock'


def command_decorator(
    exception_class,
    artifacts_key,
    error_message,
    additional_section_name=None,
):
    def method_wrapper(fn):
        @wraps(fn)
        def inner_wrapper(*args, **kwargs):
            self, *args = args
            if not self._work_dir or not os.path.exists(self._work_dir):
                return
            start = datetime.datetime.utcnow()
            exit_code, stdout, stderr = fn(self, *args, **kwargs)
            finish = datetime.datetime.utcnow()
            add_to = self._artifacts
            key = kwargs.get('artifacts_key') or artifacts_key
            section_name = (
                kwargs.get('additional_section_name')
                or additional_section_name
            )
            if section_name:
                if section_name not in self._artifacts:
                    self._artifacts[section_name] = {}
                add_to = self._artifacts[section_name]
            add_to[key] = {
                'exit_code': exit_code,
                'stderr': stderr,
                'stdout': stdout,
            }
            self._stats[key] = {
                'start_ts': start.isoformat(),
                'finish_ts': finish.isoformat(),
                'delta': (finish - start).total_seconds(),
            }
            if exit_code != 0:
                self._logger.error(
                    '%s, exit code: %s, error:\n%s',
                    error_message,
                    exit_code,
                    stderr,
                )
                raise exception_class(error_message)
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

    def __init__(
        self,
        task_id: str,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        artifacts_uploader: Optional[BaseLogsUploader] = None,
        test_configuration: Optional[dict] = None,
    ):
        # Environment ID and working directory preparation
        self._task_id = task_id
        self._vm_ip = None
        self._test_configuration = test_configuration
        self._logger = self.init_test_task_logger(task_id, dist_arch)
        self._task_log_file = None
        self._task_log_handler = None
        self._work_dir = None
        self._artifacts_dir = None
        self._inventory_file_path = None
        self._integrity_tests_dir = None
        self._class_resources_dir = os.path.join(RESOURCES_DIR, self.TYPE)
        self._template_lookup = TemplateLookup(
            directories=[RESOURCES_DIR, self._class_resources_dir]
        )
        self._uploader = artifacts_uploader
        if not artifacts_uploader:
            if not CONFIG.logs_uploader_config.skip_artifacts_upload:
                self._uploader = PulpLogsUploader(
                    CONFIG.logs_uploader_config.pulp_host,
                    CONFIG.logs_uploader_config.pulp_user,
                    CONFIG.logs_uploader_config.pulp_password,
                    concurrency=CONFIG.logs_uploader_config.uploader_concurrency,
                )

        # Basic commands and tools setup
        self._ansible_connection_type = 'ssh'

        # Package-specific variables that define needed container/VM
        self._dist_name = dist_name.lower()
        self._dist_version = str(dist_version).lower()
        self._dist_arch = dist_arch.lower()
        self._env_name = re.sub(
            r'\.',
            '_',
            f'alts_{self.TYPE}_{self.dist_name}_'
            f'{self.dist_version}_{self.dist_arch}_{task_id}',
        )

        # Package installation and test stuff
        self._repositories = repositories or []
        self.add_credentials_in_deb_repos()

        self._artifacts = {}
        self._uploaded_logs = None
        self._stats = {}

    @property
    def artifacts(self):
        return self._artifacts

    @property
    def uploaded_logs(self) -> Dict[str, str]:
        return self._uploaded_logs

    @property
    def pkg_manager(self):
        if self._dist_name == 'fedora' or (
            self._dist_name in CONFIG.rhel_flavors
            and self._dist_version.startswith('8')
        ):
            return 'dnf'
        if self._dist_name in CONFIG.rhel_flavors:
            return 'yum'
        if self._dist_name in CONFIG.debian_flavors:
            return 'apt-get'
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

    @property
    def stats(self):
        return self._stats

    @property
    def vm_ip(self):
        return self._vm_ip

    @property
    def default_ssh_params(self) -> Dict[str, Any]:
        return {
            'host': self.vm_ip or '',
            'username': 'root',
            'client_keys_files': ['~/.ssh/id_rsa.pub'],
            'disable_known_hosts_check': True,
            'ignore_encrypted_keys': True,
        }

    def add_credentials_in_deb_repos(self):
        for repo in self._repositories:
            if '-br' not in repo['name'] or 'amd64' not in repo['url']:
                continue
            parsed = urllib.parse.urlparse(repo['url'])
            netloc = f'alts:{CONFIG.bs_token}@{parsed.netloc}'
            url = urllib.parse.urlunparse(
                (
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
            repo['url'] = f'deb {url} ./'

    def __init_task_logger(self, log_file):
        """
        Task logger initialization, configures a test task   logger to write
        output to the given log file.

        Parameters
        ----------
        log_file : str
            Task log file path.

        Returns
        -------
        logging.Handler
            Task logging handler.
        """
        handler = logging.StreamHandler(
            gzip.open(log_file, 'wt', encoding='utf-8'),
        )
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s]: %(message)s",
            "%H:%M:%S %d.%m.%y",
        )
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)
        return handler

    def __close_task_logger(self, task_handler):
        """
        Closes the specified task log handler and removes it from the current
        test task logger.

        Parameters
        ----------
        task_handler : logging.Handler
            Task log handler.
        """
        task_handler.flush()
        task_handler.close()
        self._logger.handlers.remove(task_handler)

    # TODO: Think of better implementation
    def _create_work_dir(self):
        if not self._work_dir or not os.path.exists(self._work_dir):
            self._work_dir = Path(
                tempfile.mkdtemp(prefix=self.TEMPFILE_PREFIX)
            )
        return self._work_dir

    # TODO: Think of better implementation
    def _create_artifacts_dir(self):
        if not self._work_dir:
            self._work_dir = self._create_work_dir()
        path = os.path.join(self._work_dir, 'artifacts')
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

    def _create_ansible_inventory_file(self):
        self._inventory_file_path = os.path.join(
            self._work_dir,
            self.ANSIBLE_INVENTORY_FILE,
        )
        self._render_template(
            f'{self.ANSIBLE_INVENTORY_FILE}.tmpl',
            self._inventory_file_path,
            env_name=self.env_name,
            vm_ip=self.vm_ip,
            connection_type=self.ansible_connection_type,
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

    def _detect_full_package_name(
        self,
        package_name: str,
        package_version: Optional[str] = None,
    ) -> str:
        full_pkg_name = package_name
        if package_version:
            delimiter = '='
            if self.pkg_manager in ('yum', 'dnf'):
                delimiter = '-'
            full_pkg_name = f'{package_name}{delimiter}{package_version}'
        return full_pkg_name

    # First step
    def prepare_work_dir_files(self, create_ansible_inventory=False):
        # In case if you've removed worker folder, recreate one
        if not self._work_dir or not os.path.exists(self._work_dir):
            self._work_dir = self._create_work_dir()
            self._artifacts_dir = self._create_artifacts_dir()
        try:
            # Write resources that are not templated into working directory
            for ansible_file in (self.ANSIBLE_CONFIG, self.ANSIBLE_PLAYBOOK):
                shutil.copy(
                    os.path.join(RESOURCES_DIR, ansible_file),
                    os.path.join(self._work_dir, ansible_file),
                )
            shutil.copy(
                os.path.join(self._class_resources_dir, self.TF_VERSIONS_FILE),
                os.path.join(self._work_dir, self.TF_VERSIONS_FILE),
            )
            # Copy integrity tests into working directory
            self._integrity_tests_dir = os.path.join(
                self._work_dir,
                self.INTEGRITY_TESTS_DIR,
            )
            shutil.copytree(
                os.path.join(RESOURCES_DIR, self.INTEGRITY_TESTS_DIR),
                self._integrity_tests_dir,
            )

            self._create_ansible_inventory_file()
            self._render_tf_main_file()
            self._render_tf_variables_file()
        except Exception as e:
            raise WorkDirPreparationError(
                'Cannot create working directory and needed files'
            ) from e

    def run_third_party_test(
        self,
        executor: Union[AnsibleExecutor, BatsExecutor, ShellExecutor],
        cmd_args: List[str],
        docker_args: Optional[List[str]] = None,
        workdir: str = '',
        artifacts_key: str = '',
        additional_section_name: str = '',
    ):
        raise NotImplementedError

    def run_third_party_tests(self):
        if not self._test_configuration:
            return
        executors_mapping = {
            '.bats': BatsExecutor,
            '.sh': ShellExecutor,
            '.yml': AnsibleExecutor,
            '.yaml': AnsibleExecutor,
        }
        executor_params = {
            'connection_type': self.ansible_connection_type,
            'container_name': str(self.env_name),
            'logger': self._logger,
            'ssh_params': self.default_ssh_params,
        }
        for test in self._test_configuration['tests']:
            test_repo_path = self.clone_third_party_repo(test['url'])
            workdir = f'/tests/{test_repo_path.name}'
            for file in test_repo_path.iterdir():
                executor_class = executors_mapping.get(file.suffix)
                if not executor_class:
                    continue
                executor: Union[
                    AnsibleExecutor,
                    BatsExecutor,
                    ShellExecutor,
                ] = executor_class(**executor_params)
                self._logger.debug(
                    'Running repo test %s on %s...',
                    file.name,
                    self.env_name,
                )
                try:
                    self.run_third_party_test(
                        executor=executor,
                        cmd_args=[file.name],
                        docker_args=['--workdir', workdir],
                        workdir=workdir,
                        artifacts_key=f'third_party_test_{file.name}',
                        additional_section_name=(
                            TESTS_SECTION_NAME
                            if isinstance(executor, BatsExecutor)
                            else THIRD_PARTY_SECTION_NAME
                        ),
                    )
                except ThirdPartyTestError:
                    continue

    # After: prepare_work_dir_files
    @command_decorator(
        TerraformInitializationError,
        'initialize_terraform',
        'Cannot initialize terraform',
    )
    def initialize_terraform(self):
        self._logger.info(
            'Initializing Terraform environment for %s...',
            self.env_name,
        )
        self._logger.debug('Running "terraform init" command')
        lock = None
        lock_fileno = None
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
            return local['terraform'].run(
                'init',
                retcode=None,
                cwd=self._work_dir,
            )
        finally:
            if lock_fileno:
                fcntl.flock(lock_fileno, fcntl.LOCK_UN)
            if lock:
                lock.close()

    # After: initialize_terraform
    @command_decorator(
        StartEnvironmentError,
        'start_environment',
        'Cannot start environment',
    )
    def start_env(self):
        self._logger.info(
            'Starting the environment %s...',
            self.env_name,
        )
        self._logger.debug('Running "terraform apply --auto-approve" command')
        cmd_args = ['apply', '--auto-approve']
        if self.TF_VARIABLES_FILE:
            cmd_args.extend(['--var-file', self.TF_VARIABLES_FILE])
        return local['terraform'].run(
            args=cmd_args,
            retcode=None,
            cwd=self._work_dir,
        )

    # After: start_env
    @command_decorator(
        ProvisionError,
        'initial_provision',
        'Cannot run initial provision',
    )
    def initial_provision(self, verbose=False):
        # To pass dictionary into Ansible variables we need to pass
        # variables itself as a dictionary thus doing this weird
        # temporary dictionary
        var_dict = {
            'repositories': self._repositories,
            'integrity_tests_dir': self._integrity_tests_dir,
        }
        cmd_args = [
            '-i',
            self.ANSIBLE_INVENTORY_FILE,
            self.ANSIBLE_PLAYBOOK,
            '-e',
            f'{var_dict}',
            '-t',
            'initial_provision',
            '-vv',
        ]
        self._logger.info('Command args: %s', cmd_args)
        if verbose:
            cmd_args.append('-vvvv')
        cmd_args_str = ' '.join(cmd_args)
        self._logger.info('Provisioning the environment %s...', self.env_name)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )
        return local['ansible-playbook'].run(
            args=cmd_args,
            retcode=None,
            cwd=self._work_dir,
        )

    @command_decorator(
        InstallPackageError,
        'install_package',
        'Cannot install package',
    )
    def install_package(
        self,
        package_name: str,
        package_version: Optional[str] = None,
        module_name: Optional[str] = None,
        module_stream: Optional[str] = None,
        module_version: Optional[str] = None,
    ):
        full_pkg_name = self._detect_full_package_name(
            package_name,
            package_version=package_version,
        )

        self._logger.info(
            'Installing %s on %s...',
            full_pkg_name,
            self.env_name,
        )
        cmd_args = [
            '-i',
            self.ANSIBLE_INVENTORY_FILE,
            self.ANSIBLE_PLAYBOOK,
            '-e',
            f'pkg_name={full_pkg_name}',
            '-vv',
        ]
        if module_name and module_stream and module_version:
            cmd_args.extend(
                [
                    '-e',
                    f'module_name={module_name}',
                    '-e',
                    f'module_stream={module_stream}',
                    '-e',
                    f'module_version={module_version}',
                ]
            )
        cmd_args.extend(['-t', 'install_package'])
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )
        return local['ansible-playbook'].run(
            args=cmd_args,
            retcode=None,
            cwd=self._work_dir,
        )

    @command_decorator(
        UninstallPackageError,
        'uninstall_package',
        'Cannot uninstall package',
    )
    def uninstall_package(
        self,
        package_name: str,
        package_version: Optional[str] = None,
        module_name: Optional[str] = None,
        module_stream: Optional[str] = None,
        module_version: Optional[str] = None,
    ):
        if package_name in CONFIG.uninstall_excluded_pkgs:
            return

        full_pkg_name = self._detect_full_package_name(
            package_name, package_version=package_version
        )

        self._logger.info(
            'Uninstalling %s from %s...',
            full_pkg_name,
            self.env_name,
        )
        cmd_args = [
            '-i',
            self.ANSIBLE_INVENTORY_FILE,
            self.ANSIBLE_PLAYBOOK,
            '-e',
            f'pkg_name={full_pkg_name}',
            '-vv',
        ]
        if module_name and module_stream and module_version:
            cmd_args.extend(
                [
                    '-e',
                    f'module_name={module_name}',
                    '-e',
                    f'module_stream={module_stream}',
                    '-e',
                    f'module_version={module_version}',
                ]
            )
        cmd_args.extend(['-t', 'uninstall_package'])
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )
        return local['ansible-playbook'].run(
            args=cmd_args,
            retcode=None,
            cwd=self._work_dir,
        )

    @command_decorator(
        PackageIntegrityTestsError,
        'package_integrity_tests',
        'Package integrity tests failed',
        additional_section_name=TESTS_SECTION_NAME,
    )
    def run_package_integrity_tests(
        self,
        package_name: str,
        package_version: Optional[str] = None,
    ):
        """
        Run basic integrity tests for the package

        Parameters
        ----------
        package_name:       str
            Package name
        package_version:    optional, str
            Package version

        Returns
        -------
        tuple
            Exit code, stdout and stderr from executed command

        """
        cmd_args = [
            '--tap-stream',
            '--tap-files',
            '--tap-outdir',
            self._artifacts_dir,
            '--hosts',
            'ansible://all',
            '--ansible-inventory',
            self._inventory_file_path,
            '--package-name',
            package_name,
        ]
        if self.ansible_connection_type == 'ssh':
            cmd_args.append('--force-ansible')
        full_pkg_name = package_name
        if package_version:
            full_pkg_name = f'{package_name}-{package_version}'
            cmd_args.extend(('--package-version', package_version))
        cmd_args.append('tests')
        self._logger.info(
            'Running package integrity tests for %s on %s...',
            full_pkg_name,
            self.env_name,
        )
        return local['py.test'].run(
            args=cmd_args,
            retcode=None,
            cwd=self._integrity_tests_dir,
        )

    def publish_artifacts_to_storage(self):
        # Should upload artifacts from artifacts directory to preferred
        # artifacts storage (S3, Minio, etc.)

        if CONFIG.logs_uploader_config.skip_artifacts_upload:
            self._logger.warning(
                'Skipping artifacts upload due to configuration',
            )
            return

        def replace_host_name(log_string) -> str:
            return re.sub(r'\[local\]', f'[{self._task_id}', log_string)

        def write_to_file(file_base_name: str, artifacts_section: dict):
            log_file_path = os.path.join(
                self._artifacts_dir,
                f'{file_base_name}_{self._task_id}.log',
            )
            with open(log_file_path, 'wb') as fd:
                stdout = replace_host_name(artifacts_section["stdout"])
                file_content = (
                    f'\nTask ID: {self._task_id}\n'
                    f'Time: {datetime.datetime.utcnow().isoformat()}\n'
                    f'Exit code: {artifacts_section["exit_code"]}\n'
                    f'Stdout:\n\n{stdout}'
                )
                if artifacts_section.get('stderr'):
                    stderr = replace_host_name(artifacts_section["stderr"])
                    file_content += f'\n\nStderr:\n\n{stderr}'
                fd.write(gzip.compress(file_content.encode()))

        for artifact_key, content in self.artifacts.items():
            if artifact_key in TESTS_SECTIONS_NAMES:
                for inner_artifact_key, inner_content in content.items():
                    log_base_name = inner_artifact_key
                    if not log_base_name.startswith(artifact_key):
                        log_base_name = f'{artifact_key}_{inner_artifact_key}'
                    write_to_file(log_base_name, inner_content)
            elif artifact_key == 'initialize_terraform':
                stdout = content['stdout']
                content['stdout'] = f'Task ID: {self._task_id}\n\n{stdout}'
                write_to_file(artifact_key, content)
            else:
                write_to_file(artifact_key, content)

        upload_dir = os.path.join(
            CONFIG.logs_uploader_config.artifacts_root_directory,
            self._task_id,
        )
        try:
            artifacts = self._uploader.upload(
                self._artifacts_dir,
                upload_dir=upload_dir,
            )
            self._uploaded_logs = artifacts
        except UploadError as e:
            raise PublishArtifactsError from e

    def clone_third_party_repo(
        self,
        repo_url: str,
    ) -> Path:
        self._logger.debug('Cloning git repo: %s', repo_url)
        exit_code, _, stderr = local['git'].run(
            ['clone', repo_url],
            retcode=None,
            cwd=self._work_dir,
        )
        if exit_code != 0:
            raise ValueError(f'Cannot clone git repo:\n{stderr}')
        return Path(
            self._work_dir,
            Path(repo_url).name.replace('.git', ''),
        )

    # After: install_package and run_tests
    @command_decorator(
        StopEnvironmentError,
        'stop_environment',
        'Cannot destroy environment',
    )
    def stop_env(self):
        if os.path.exists(self._work_dir):
            self._logger.info(
                'Destroying the environment %s...',
                self.env_name,
            )
            self._logger.debug(
                'Running "terraform destroy --auto-approve" command'
            )
            cmd_args = ['destroy', '--auto-approve']
            if self.TF_VARIABLES_FILE:
                cmd_args.extend(['--var-file', self.TF_VARIABLES_FILE])
            return local['terraform'].run(
                args=cmd_args,
                retcode=None,
                cwd=self._work_dir,
            )

    def erase_work_dir(self):
        if self._work_dir and os.path.exists(self._work_dir):
            self._logger.info('Erasing working directory...')
            try:
                shutil.rmtree(self._work_dir)
            except Exception as e:
                self._logger.error(
                    'Error while erasing working directory: %s',
                    e,
                )
            else:
                self._logger.info('Working directory was successfully removed')

    def setup(self):
        self._stats['started_at'] = datetime.datetime.utcnow().isoformat()
        self.prepare_work_dir_files()
        self._task_log_file = os.path.join(
            self._work_dir,
            f'alts-{self._task_id}-{self._dist_arch}.log',
        )
        self._task_log_handler = self.__init_task_logger(self._task_log_file)
        self.initialize_terraform()
        self.start_env()
        self.initial_provision()

    def teardown(self, publish_artifacts: bool = True):
        try:
            self.stop_env()
        except Exception as e:
            self._logger.error('Error while stop environment: %s', e)
        if publish_artifacts:
            try:
                self.publish_artifacts_to_storage()
            except Exception as e:
                self._logger.exception(
                    'Exception while publishing artifacts: %s',
                    str(e),
                )
            finally:
                if not self._uploaded_logs:
                    self._uploaded_logs = []
                if self._task_log_handler:
                    self.__close_task_logger(self._task_log_handler)
                if (
                    self._task_log_handler
                    and not CONFIG.logs_uploader_config.skip_artifacts_upload
                ):
                    self._uploaded_logs.append(
                        self._uploader.upload_single_file(self._task_log_file)
                    )

        self.erase_work_dir()

    @staticmethod
    def init_test_task_logger(task_id, arch):
        """
        Test task logger initialization.

        Returns
        -------
        logging.Logger
            Test task logger.
        """
        logger = logging.getLogger(f'test_task-{task_id}-{arch}-logger')
        logger.handlers = []
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s: %(message)s",
            "%H:%M:%S %d.%m.%y",
        )
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def reboot_target(self, reboot_timeout: int = 120) -> bool:
        ansible = local['ansible']
        module_args = {
            'reboot_timeout': reboot_timeout,
        }
        cmd_args = (
            '-i',
            str(self.ANSIBLE_INVENTORY_FILE),
            '-m',
            'reboot',
            '-a',
            f'"{json.dumps(module_args)}"',
            'all',
        )
        exit_code, stdout, stderr = ansible.run(
            args=cmd_args,
            retcode=None,
            cwd=self._work_dir,
        )
        if exit_code == 0:
            return True
        self._logger.error(
            'Unable to connect to VM. Stdout: %s\nStderr: %s',
            stdout,
            stderr,
        )
        return False


class GenericVMRunner(BaseRunner):
    def __init__(
        self,
        task_id: str,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        test_configuration: Optional[dict] = None,
    ):
        super().__init__(
            task_id,
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            test_configuration=test_configuration,
        )
        ssh_key_path = os.path.abspath(
            os.path.expanduser(CONFIG.ssh_public_key_path)
        )
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
        exit_code = 0
        while retries > 0:
            exit_code, stdout, stderr = ansible.run(
                args=cmd_args,
                retcode=None,
                cwd=self._work_dir,
            )
            if exit_code == 0:
                return exit_code, stdout, stderr
            retries -= 1
            time.sleep(10)
        self._logger.error(
            'Unable to connect to VM. Stdout: %s\nStderr: %s',
            stdout,
            stderr,
        )
        return exit_code, stdout, stderr

    @command_decorator(
        StartEnvironmentError,
        'start_environment',
        'Cannot start environment',
    )
    def start_env(self):
        super().start_env()
        # VM gets its IP address only after deploy.
        # To extract it, the `vm_ip` output should be defined
        # in Terraform main file.
        exit_code, stdout, stderr = local['terraform'].run(
            args=('output', '-raw', 'vm_ip'),
            retcode=None,
            cwd=self._work_dir,
        )
        if exit_code != 0:
            error_message = f'Cannot get VM IP: {stderr}'
            self._logger.error(error_message)
            return exit_code, stdout, stderr
        self._vm_ip = stdout
        # Because we don't know about a VM's IP before its creating
        # And get an IP after launch of terraform script
        self._create_ansible_inventory_file()
        self._logger.info('Waiting for SSH port to be available')
        exit_code, stdout, stderr = self._wait_for_ssh()
        if exit_code:
            self._logger.error(
                'Machine %s is started, but SSH connection is not working',
                self.env_name,
            )
        else:
            self._logger.info('Machine is available for SSH connection')
        return exit_code, stdout, stderr
