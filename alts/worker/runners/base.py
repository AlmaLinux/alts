import datetime
import fcntl
import gzip
import logging
import os
import random
import re
import shutil
import tempfile
import time
import urllib.parse
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from mako.lookup import TemplateLookup
from plumbum import local, ProcessExecutionError, ProcessTimedOut

from alts.shared.exceptions import (
    AbortedTestTask,
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
from alts.shared.utils.asyncssh import AsyncSSHClient
from alts.shared.utils.git_utils import (
    clone_gerrit_repo,
    clone_git_repo,
    git_reset_hard,
    prepare_gerrit_command,
)
from alts.worker import CONFIG, RESOURCES_DIR
from alts.worker.executors.ansible import AnsibleExecutor
from alts.worker.executors.bats import BatsExecutor
from alts.worker.executors.command import CommandExecutor
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
BASE_SYSTEM_INFO_COMMANDS = {
    'Current disk space usage': 'df -h',
    'Kernel version': 'uname -a',
    'Environment IP': 'ip a',
    'Environment uptime': 'uptime',
}


def command_decorator(
    artifacts_key,
    error_message,
    exception_class=None,
    additional_section_name=None,
    is_abortable=True,
):
    def method_wrapper(fn):
        @wraps(fn)
        def inner_wrapper(*args, **kwargs):
            self, *args = args
            if is_abortable and not self.already_aborted:
                self._raise_if_aborted()
            if not self._work_dir or not os.path.exists(self._work_dir):
                return
            start = datetime.datetime.utcnow()
            exit_code, stdout, stderr = fn(self, *args, **kwargs)
            finish = datetime.datetime.utcnow()
            add_to = self._artifacts
            key = kwargs.get('artifacts_key', artifacts_key)
            section_name = kwargs.get(
                'additional_section_name',
                additional_section_name,
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
            if exit_code != 0 and exception_class:
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
    ANSIBLE_TEMPLATES_DIR = 'templates'
    ANSIBLE_INVENTORY_FILE = 'hosts'
    TEMPFILE_PREFIX = 'base_test_runner_'
    INTEGRITY_TESTS_DIR = 'package_tests'
    INIT_TESTS = frozenset(['0_init', '0_init.yml'])

    already_aborted: bool = False

    def __init__(
        self,
        task_id: str,
        task_is_aborted: Callable,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        artifacts_uploader: Optional[BaseLogsUploader] = None,
        test_configuration: Optional[dict] = None,
        verbose: bool = False,
    ):
        # Environment ID and working directory preparation
        self._task_id = task_id
        self._task_is_aborted = task_is_aborted
        self._vm_ip = None
        self._test_configuration = test_configuration or {}
        self._test_env = self._test_configuration.get('test_env') or {}
        self._logger = self.init_test_task_logger(
            task_id,
            dist_arch,
            verbose=verbose,
        )
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
        if (
            not artifacts_uploader
            and not CONFIG.logs_uploader_config.skip_artifacts_upload
        ):
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
        repos = repositories or []
        self._repositories = self.prepare_repositories(repos)
        self.add_credentials_to_build_repos()

        self._artifacts = {}
        self._uploaded_logs = None
        self._stats = {}
        self._verbose = verbose

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
            and self._dist_version.startswith(('8', '9', '10'))
        ):
            return 'dnf'
        if self._dist_name in CONFIG.rhel_flavors:
            return 'yum'
        if self._dist_name in CONFIG.debian_flavors:
            return 'apt-get'
        raise ValueError(f'Unknown distribution: {self._dist_name}')

    @property
    def ansible_connection_type(self) -> str:
        return self._ansible_connection_type

    @property
    def pytest_is_needed(self) -> bool:
        return self._test_env.get('pytest_is_needed', True)

    @property
    def use_deprecated_ansible(self) -> bool:
        return self._test_env.get('use_deprecated_ansible', False)

    @property
    def ansible_binary(self) -> str:
        if self.use_deprecated_ansible:
            return os.path.join(CONFIG.deprecated_ansible_venv, 'bin', 'ansible')
        return 'ansible'

    @property
    def ansible_playbook_binary(self) -> str:
        if self.use_deprecated_ansible:
            return os.path.join(
                CONFIG.deprecated_ansible_venv,
                'bin',
                'ansible-playbook'
            )
        return 'ansible-playbook'

    @property
    def vm_disk_size(self) -> int:
        return self._test_env.get(
            'vm_disk_size',
            CONFIG.opennebula_config.default_vm_disk_size,
        )

    @property
    def vm_ram_size(self) -> int:
        return self._test_env.get(
            'vm_ram_size',
            CONFIG.opennebula_config.default_vm_ram_size,
        )

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
        max_keepalive_msgs = (
            CONFIG.tests_exec_timeout // CONFIG.keepalive_interval
        ) + 5
        return {
            'host': self.vm_ip or '',
            'username': 'root',
            'client_keys_files': ['~/.ssh/id_rsa.pub'],
            'disable_known_hosts_check': True,
            'ignore_encrypted_keys': True,
            'logging_level': 'DEBUG' if self._verbose else 'INFO',
            'keepalive_interval': CONFIG.keepalive_interval,
            'keepalive_count_max': max_keepalive_msgs,
        }

    def prepare_repositories(self, repositories: List[dict]) -> List[dict]:
        if self.dist_name in CONFIG.rhel_flavors:
            return repositories
        for repo in repositories:
            self._logger.debug('Repository initial state: %s', repo)
            if not repo['url'].startswith('deb'):
                continue
            url_parts = repo['url'].split(' ')
            if url_parts[1].startswith('['):
                continue
            url_parts.insert(1, f'[arch={self.dist_arch}]')
            repo['url'] = ' '.join(url_parts)
            self._logger.debug('Repository modified state: %s', repo)
        return repositories

    def add_credentials_to_build_repos(self):
        for repo in self._repositories:
            if '-br' not in repo['name']:
                continue
            parsed = urllib.parse.urlparse(repo['url'])
            netloc = parsed.netloc
            if CONFIG.bs_token:
                netloc = f'alts:{CONFIG.bs_token}@{parsed.netloc}'
            url = urllib.parse.urlunparse((
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))
            if (self.dist_name in CONFIG.debian_flavors
                    and not repo['url'].startswith('deb ')):
                url = f'deb {url} ./'
                self._logger.info('Modified repo url: %s', url)
            repo['url'] = url
        self._logger.info('Repositories: %s', self._repositories)

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
        try:
            task_handler.flush()
            task_handler.close()
            self._logger.handlers.remove(task_handler)
        except ValueError:
            pass

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
            copy_list = [
                self.ANSIBLE_CONFIG,
                self.ANSIBLE_PLAYBOOK,
                self.ANSIBLE_TEMPLATES_DIR,
            ]
            # Write resources that are not templated into working directory
            for ansible_file in copy_list:
                src_path = os.path.join(RESOURCES_DIR, ansible_file)
                dst_path = os.path.join(self._work_dir, ansible_file)
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy(src_path, dst_path)
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

    def get_test_executor_params(self) -> dict:
        return {
            'connection_type': self.ansible_connection_type,
            'container_name': str(self.env_name),
            'logger': self._logger,
            'ssh_params': self.default_ssh_params,
        }

    def __terraform_init(self):
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
            return local['terraform'].with_cwd(self._work_dir).run(
                ('init', '-no-color'),
                timeout=CONFIG.provision_timeout,
            )
        finally:
            if lock_fileno:
                fcntl.flock(lock_fileno, fcntl.LOCK_UN)
            if lock:
                lock.close()

    # After: prepare_work_dir_files
    @command_decorator(
        'initialize_terraform',
        'Cannot initialize terraform',
        exception_class=TerraformInitializationError,
    )
    def initialize_terraform(self):
        self._logger.info(
            'Initializing Terraform environment for %s...',
            self.env_name,
        )
        self._logger.debug('Running "terraform init" command')
        attempts = 5
        recorded_exc = None
        while attempts > 0:
            try:
                return self.__terraform_init()
            except Exception as e:
                recorded_exc = e
                attempts -= 1
                time.sleep(random.randint(5, 10))
        if attempts == 0 and recorded_exc:
            return 1, '', str(recorded_exc)

    # After: initialize_terraform
    @command_decorator(
        'start_environment',
        'Cannot start environment',
        exception_class=StartEnvironmentError,
    )
    def start_env(self):
        self._logger.info(
            'Starting the environment %s...',
            self.env_name,
        )
        self._logger.debug('Running "terraform apply --auto-approve" command')
        cmd_args = ['apply', '--auto-approve', '-no-color']
        if self.TF_VARIABLES_FILE:
            cmd_args.extend(['--var-file', self.TF_VARIABLES_FILE])
        return local['terraform'].with_cwd(self._work_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )

    # After: start_env
    @command_decorator(
        'initial_provision',
        'Cannot run initial provision',
        exception_class=ProvisionError,
    )
    def initial_provision(self, verbose=False):
        # To pass dictionary into Ansible variables we need to pass
        # variables itself as a dictionary thus doing this weird
        # temporary dictionary
        var_dict = {
            'repositories': self._repositories,
            'integrity_tests_dir': self._integrity_tests_dir,
            'connection_type': self.ansible_connection_type,
            'pytest_is_needed': self.pytest_is_needed,
            'development_mode': CONFIG.development_mode,
            'centos_6_epel_release_url': CONFIG.centos_6_epel_release_url,
            'package_proxy': CONFIG.package_proxy,
        }
        cmd_args = [
            '-i',
            self.ANSIBLE_INVENTORY_FILE,
            self.ANSIBLE_PLAYBOOK,
            '-e',
            f'{var_dict}',
            '-t',
            'initial_provision',
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
        return local[self.ansible_playbook_binary].with_cwd(self._work_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )

    def get_system_info_commands_list(self) -> Dict[str, str]:
        self._logger.debug('Returning default system info commands list')
        basic_commands = BASE_SYSTEM_INFO_COMMANDS.copy()
        if self._dist_name in CONFIG.rhel_flavors:
            basic_commands['Installed packages'] = 'rpm -qa'
            basic_commands['Repositories list'] = (
                f'{self.pkg_manager} repolist'
            )
            basic_commands['Repositories details'] = (
                'find /etc/yum.repos.d/ -type f -exec cat {} +'
            )
        else:
            basic_commands['Installed packages'] = 'dpkg -l'
            basic_commands['Repositories list'] = 'apt-cache policy'
            basic_commands['Repositories details'] = (
                'find /etc/apt/ -type f -name *.list* -o -name *.sources* '
                '-exec cat {} +'
            )
        return basic_commands

    @command_decorator(
        'system_info',
        'System information commands block is failed',
    )
    def run_system_info_commands(self):
        self._logger.info('Starting system info section')
        errored_commands = {}
        successful_commands = {}
        error_output = ''
        executor_params = self.get_test_executor_params()
        executor_params['timeout'] = CONFIG.commands_exec_timeout
        for section, cmd in self.get_system_info_commands_list().items():
            try:
                cmd_as_list = cmd.split(' ')
                binary, *args = cmd_as_list
                result = CommandExecutor(binary, **executor_params).run(args)
                output = '\n'.join([result.stdout, result.stderr])
                if result.is_successful():
                    successful_commands[section] = output
                else:
                    errored_commands[section] = output
            except Exception as e:
                errored_commands[section] = str(e)
        success_output = '\n\n'.join((
            section + '\n' + section_out
            for section, section_out in successful_commands.items()
        ))
        if errored_commands:
            error_output = '\n\n'.join((
                section + '\n' + section_out
                for section, section_out in errored_commands.items()
            ))
        final_output = f'System info commands:\n{success_output}'
        if error_output:
            final_output += (
                f'\n\nCommands that have failed to run:\n{error_output}'
            )
        self._logger.info('System info section is finished')
        return 0, final_output, ''

    @command_decorator(
        'install_package',
        'Cannot install package',
        exception_class=InstallPackageError,
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
        ]
        if module_name and module_stream and module_version:
            cmd_args.extend([
                '-e',
                f'module_name={module_name}',
                '-e',
                f'module_stream={module_stream}',
                '-e',
                f'module_version={module_version}',
            ])
        cmd_args.extend(['-t', 'install_package'])
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )
        return local[self.ansible_playbook_binary].with_cwd(self._work_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )

    @command_decorator(
        'uninstall_package',
        'Cannot uninstall package',
        exception_class=UninstallPackageError,
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
            return 0, '', ''

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
        ]
        if module_name and module_stream and module_version:
            cmd_args.extend([
                '-e',
                f'module_name={module_name}',
                '-e',
                f'module_stream={module_stream}',
                '-e',
                f'module_version={module_version}',
            ])
        cmd_args.extend(['-t', 'uninstall_package'])
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )
        return local[self.ansible_playbook_binary].with_cwd(self._work_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )

    @command_decorator(
        'package_integrity_tests',
        'Package integrity tests failed',
        exception_class=PackageIntegrityTestsError,
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
        return local['py.test'].with_cwd(self._integrity_tests_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.tests_exec_timeout,
        )

    @staticmethod
    def prepare_gerrit_repo_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if CONFIG.gerrit_username:
            netloc = f'{CONFIG.gerrit_username}@{parsed.netloc}'
        else:
            netloc = parsed.netloc
        return urllib.parse.urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    def clone_third_party_repo(
        self,
        repo_url: str,
        git_ref: str,
    ) -> Optional[Path]:
        git_repo_path = None
        if repo_url.endswith('.git'):
            func = clone_git_repo
        elif 'gerrit' in repo_url:
            func = clone_gerrit_repo
        else:
            self._logger.debug('An unknown repository format, skipping')
            return git_repo_path
        repo_name = os.path.basename(repo_url)
        repo_reference_dir = None
        if CONFIG.git_reference_directory:
            repo_reference_dir = os.path.join(
                CONFIG.git_reference_directory, repo_name)
        try:
            return func(
                repo_url,
                git_ref,
                self._work_dir,
                self._logger,
                reference_directory=repo_reference_dir
            )
        except (ProcessExecutionError, ProcessTimedOut):
            return None

    def run_third_party_test(
        self,
        executor: Union[AnsibleExecutor, BatsExecutor, ShellExecutor],
        cmd_args: List[str],
        docker_args: Optional[List[str]] = None,
        workdir: str = '',
        artifacts_key: str = '',
        additional_section_name: str = '',
        env_vars: Optional[List[str]] = None,
    ):
        raise NotImplementedError

    def sort_tests(self, tests_dir: Path) -> List[Path]:
        tests_list = list(tests_dir.iterdir())
        tests_list.sort()
        organized_tests_list = []
        init = None
        install = None
        for test in tests_list:
            if test.is_dir():
                # Usually ansible directory will contain 0_init.yml
                if test.name == 'ansible':
                    ansible_init = Path(test, '0_init.yml')
                    if ansible_init.exists():
                        init = ansible_init
                continue
            if test.name in self.INIT_TESTS:
                init = test
                continue
            elif test.name == '0_install':
                install = test
                continue
            else:
                organized_tests_list.append(test)
        # 0_init should be the first, 0_install should be the second
        if install:
            organized_tests_list.insert(0, install)
        if init:
            organized_tests_list.insert(0, init)
        return organized_tests_list

    @command_decorator(
        f'{THIRD_PARTY_SECTION_NAME}_tests_wrapper',
        'Preparation/running third party tests has failed',
        ThirdPartyTestError,
    )
    def run_third_party_tests(self):
        if not self._test_configuration:
            return 0, 'Nothing to run', ''
        errors = []
        executors_mapping = {
            '.bats': BatsExecutor,
            '.sh': ShellExecutor,
            '.yml': AnsibleExecutor,
            '.yaml': AnsibleExecutor,
            '': ShellExecutor,
        }
        executor_params = self.get_test_executor_params()
        executor_params['timeout'] = CONFIG.tests_exec_timeout
        for test in self._test_configuration['tests']:
            git_ref = test.get('git_ref', 'master')
            repo_url = test['url']
            test_dir = test['test_dir']
            tests_to_run = test.get('tests_to_run', [])
            repo_url = (
                self.prepare_gerrit_repo_url(repo_url)
                if 'gerrit' in repo_url
                else repo_url
            )
            test_repo_path = self.clone_third_party_repo(repo_url, git_ref)
            if not test_repo_path:
                errors.append(f'Cannot clone test repository {repo_url}')
                continue
            workdir = os.path.join(
                CONFIG.tests_base_dir,
                test_repo_path.name,
                test_dir,
            )
            tests_list = self.sort_tests(Path(test_repo_path, test_dir))
            for test_file in tests_list:
                if tests_to_run and test_file.name not in tests_to_run:
                    continue
                executor_class = executors_mapping.get(test_file.suffix)
                if not executor_class:
                    self._logger.warning(
                        'Cannot get executor for test %s',
                        test_file
                    )
                    continue
                executor: Union[
                    AnsibleExecutor,
                    BatsExecutor,
                    ShellExecutor,
                ] = executor_class(**executor_params)
                self._logger.debug(
                    'Running the third party test %s on %s...',
                    test_file.name,
                    self.env_name,
                )
                try:
                    key = f'{THIRD_PARTY_SECTION_NAME}_test_{test_file.name}'
                    self.run_third_party_test(
                        executor=executor,
                        cmd_args=[test_file.name],
                        docker_args=['--workdir', workdir],
                        workdir=workdir,
                        artifacts_key=key,
                        additional_section_name=THIRD_PARTY_SECTION_NAME,
                        env_vars=self._test_env.get('extra_env_vars', []),
                    )
                except ThirdPartyTestError:
                    errors.append(f'Test {test_file.name} has failed')
                except Exception:
                    self._logger.exception(
                        'An unknown error occurred during the test execution'
                    )
            git_reset_hard(test_repo_path, self._logger)
        if errors:
            return 1, '', '\n'.join(errors)
        return 0, 'Third-party tests have passed', ''

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
                stdout_ = replace_host_name(artifacts_section["stdout"])
                file_content = (
                    f'\nTask ID: {self._task_id}\n'
                    f'Time: {datetime.datetime.utcnow().isoformat()}\n'
                    f'Exit code: {artifacts_section["exit_code"]}\n'
                    f'Stdout:\n\n{stdout_}'
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

    # After: install_package and run_tests
    @command_decorator(
        'stop_environment',
        'Cannot destroy environment',
        exception_class=StopEnvironmentError,
        is_abortable=False,
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
            cmd_args = ['destroy', '--auto-approve', '-no-color']
            if self.TF_VARIABLES_FILE:
                cmd_args.extend(['--var-file', self.TF_VARIABLES_FILE])
            return local['terraform'].with_cwd(self._work_dir).run(
                args=cmd_args,
                retcode=None,
                timeout=CONFIG.provision_timeout,
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

    def setup(self, skip_provision: bool = False):
        self._stats['started_at'] = datetime.datetime.utcnow().isoformat()
        self.prepare_work_dir_files()
        self._task_log_file = os.path.join(
            self._work_dir,
            f'alts-{self._task_id}-{self._dist_arch}.log',
        )
        self._task_log_handler = self.__init_task_logger(self._task_log_file)
        self.initialize_terraform()
        self.start_env()
        if not skip_provision:
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
                    if not CONFIG.logs_uploader_config.skip_artifacts_upload:
                        self._uploaded_logs.append(
                            self._uploader.upload_single_file(
                                self._task_log_file
                            )
                        )

        self.erase_work_dir()

    @staticmethod
    def init_test_task_logger(task_id, arch, verbose: bool = False):
        """
        Test task logger initialization.

        Returns
        -------
        logging.Logger
            Test task logger.
        """
        logger = logging.getLogger(f'test_task-{task_id}-{arch}-logger')
        logger.handlers = []
        log_level = logging.DEBUG if verbose else logging.INFO
        logger.setLevel(log_level)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s: %(message)s",
            "%H:%M:%S %d.%m.%y",
        )
        handler = logging.StreamHandler()
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def reboot_target(self, reboot_timeout: int = 300) -> bool:
        ansible = local[self.ansible_binary]
        cmd_args = (
            '-i',
            str(self.ANSIBLE_INVENTORY_FILE),
            '-m',
            'reboot',
            '-a',
            f'reboot_timeout={reboot_timeout}',
            'all',
        )
        exit_code, stdout, stderr = ansible.with_cwd(self._work_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )
        if exit_code == 0:
            return True
        self._logger.error(
            'Unable to connect to VM. Stdout: %s\nStderr: %s',
            stdout,
            stderr,
        )
        return False

    def _raise_if_aborted(self):
        if self._task_is_aborted():
            self.already_aborted = True
            raise AbortedTestTask


class GenericVMRunner(BaseRunner):
    VM_RESTART_OUTPUT_TRIGGER = '>>>VMRESTART<<<'

    def __init__(
        self,
        task_id: str,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        test_configuration: Optional[dict] = None,
        verbose: bool = False,
    ):
        super().__init__(
            task_id,
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            test_configuration=test_configuration,
            verbose=verbose,
        )
        self._tests_dir = CONFIG.tests_base_dir
        self._ssh_client: Optional[AsyncSSHClient] = None

    def _wait_for_ssh(self, retries=60):
        ansible = local[self.ansible_binary]
        cmd_args = ('-i', self.ANSIBLE_INVENTORY_FILE, '-m', 'ping', 'all')
        stdout = None
        stderr = None
        exit_code = 0
        while retries > 0:
            exit_code, stdout, stderr = ansible.with_cwd(self._work_dir).run(
                args=cmd_args,
                retcode=None,
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
        'start_environment',
        'Cannot start environment',
        exception_class=StartEnvironmentError,
        is_abortable=False,
    )
    def start_env(self):
        super().start_env()
        # VM gets its IP address only after deploy.
        # To extract it, the `vm_ip` output should be defined
        # in Terraform main file.
        exit_code, stdout, stderr = local['terraform'].with_cwd(
            self._work_dir).run(
            args=('output', '-raw',  '-no-color', 'vm_ip'),
            retcode=None,
            timeout=CONFIG.provision_timeout,
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

    def setup(self, skip_provision: bool = False):
        super().setup(skip_provision=skip_provision)
        params = self.default_ssh_params
        params['timeout'] = CONFIG.provision_timeout
        self._ssh_client = AsyncSSHClient(**params)

    def clone_third_party_repo(
            self,
            repo_url: str,
            git_ref: str,
    ) -> Optional[Path]:
        git_repo_path = super().clone_third_party_repo(repo_url, git_ref)
        if not git_repo_path:
            return
        if self._ssh_client:
            repo_path = Path(
                self._tests_dir,
                Path(repo_url).name.replace('.git', ''),
            )
            cmd = (f'[ if -e {repo_path} ] then; cd {repo_path} && git pull; '
                   f'else cd {self._tests_dir} && git clone {repo_url}; fi')
            self._ssh_client.sync_run_command(cmd)
            repo_path = Path(
                self._tests_dir,
                Path(repo_url).name.replace('.git', ''),
            )
            command = f'git fetch origin && git checkout {git_ref}'
            if 'gerrit' in repo_url:
                command = prepare_gerrit_command(git_ref)
            result = self._ssh_client.sync_run_command(
                f'cd {repo_path} && {command}',
            )
            if not result.is_successful():
                self._logger.error(
                    'Cannot prepare git repository:\n%s',
                    result.stderr,
                )
                return
        return git_repo_path

    @command_decorator(
        '',
        'Third party tests failed',
        exception_class=ThirdPartyTestError,
    )
    def run_third_party_test(
            self,
            executor: Union[AnsibleExecutor, BatsExecutor, ShellExecutor],
            cmd_args: List[str],
            docker_args: Optional[List[str]] = None,
            workdir: str = '',
            artifacts_key: str = '',
            additional_section_name: str = '',
            env_vars: Optional[List[str]] = None,
    ):
        result = executor.run_ssh_command(
            cmd_args=cmd_args,
            workdir=workdir,
            env_vars=env_vars,
        )
        if (self.VM_RESTART_OUTPUT_TRIGGER in result.stdout
                or self.VM_RESTART_OUTPUT_TRIGGER in result.stderr):
            reboot_result = self.reboot_target()
            if not reboot_result:
                exit_code = 1
                stderr = result.stderr + '\n\nReboot failed'
                return exit_code, result.stdout, stderr
        return result.model_dump().values()
