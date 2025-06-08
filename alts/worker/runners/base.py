import datetime
import gzip
import logging
import os
import random
import re
import signal
import shutil
import tempfile
import time
import traceback
import urllib.parse
from functools import wraps
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Union,
    Tuple,
    Type,
)

from billiard.exceptions import SoftTimeLimitExceeded
from filelock import FileLock
from mako.lookup import TemplateLookup
from plumbum import local, ProcessExecutionError, ProcessTimedOut

from alts.shared.constants import COMMAND_TIMEOUT_EXIT_CODE
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
from alts.shared.utils.asyncssh import AsyncSSHClient, LongRunSSHClient
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
BASE_SYSTEM_INFO_COMMANDS: Dict[str, Tuple[str, ...]] = {
    'List of block devices': ('lsblk',),
    'Current disk space usage': ('df', '-h'),
    'Kernel version': ('uname', '-a'),
    'Environment IP': ('ip', 'a'),
    'Environment uptime': ('uptime',),
}
FILE_TYPE_REGEXES_MAPPING = {
    r'.*(bats).*': BatsExecutor,
    r'.*(Bourne-Again shell).*': ShellExecutor,
    r'.*(python).*': CommandExecutor,
}
INTERPRETER_REGEX = re.compile(
    r'^#!(?P<python_interpreter>.*(python[2-4]?))(?P<options> .*)?'
)
EXECUTORS_MAPPING = {
    '.bash': ShellExecutor,
    '.bats': BatsExecutor,
    '.py': CommandExecutor,
    '.sh': ShellExecutor,
    '.yml': AnsibleExecutor,
    '.yaml': AnsibleExecutor,
}

DetectExecutorResult = Type[Optional[Union[
    AnsibleExecutor,
    BatsExecutor,
    CommandExecutor,
    ShellExecutor,
]]]


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
            try:
                exit_code, stdout, stderr = fn(self, *args, **kwargs)
            except SoftTimeLimitExceeded:
                exit_code = 1
                stdout = ''
                stderr = 'Task timeout has exceeded'
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
        x86_64_v2=['x86_64_v2'],
        ppc64le=['ppc64le'],
        s390x=['s390x'],
    )
    COST = 0
    TF_VARIABLES_FILE = None
    TF_MAIN_FILE = None
    TF_VERSIONS_FILE = 'versions.tf'
    ANSIBLE_PLAYBOOK = 'playbook.yml'
    ANSIBLE_FILES = [
        'ansible.cfg',
        ANSIBLE_PLAYBOOK,
        'roles',
        'templates',
    ]
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
        package_channel: Optional[str] = None,
        test_configuration: Optional[dict] = None,
        test_flavor: Optional[Dict[str, str]] = None,
        vm_alive: bool = False,
        verbose: bool = False,
    ):
        # Environment ID and working directory preparation
        self._task_id = task_id
        self._task_is_aborted = task_is_aborted
        self._test_configuration = test_configuration or {}
        self._test_flavor = test_flavor or {}
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
        if self.test_flavor:
            flavor_name = self.test_flavor['name']
            flavor_version = self.test_flavor['version']
            self._env_name = re.sub(
                r'\.',
                '_',
                f'alts_{self.TYPE}_{self.dist_name}_'
                f'{self.dist_version}_{flavor_name}_{flavor_version}_'
                f'{self.dist_arch}_{task_id}',
            )
        else:
            self._env_name = re.sub(
                r'\.',
                '_',
                f'alts_{self.TYPE}_{self.dist_name}_'
                f'{self.dist_version}_{self.dist_arch}_{task_id}',
            )

        # Package installation and test stuff
        repos = repositories.copy() if repositories is not None else []
        if CONFIG.authorize_build_repositories:
            repos = self.add_credentials_to_build_repos(repos)
        self._repositories = self.prepare_repositories(repos)
        self._artifacts = {}
        self._uploaded_logs = None
        self._stats = {}
        self._verbose = verbose
        self.package_channel = package_channel
        self.vm_alive = vm_alive

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
    def test_flavor(self) -> Dict[str, str]:
        return self._test_flavor

    @property
    def test_configuration(self) -> dict:
        return self._test_configuration

    @property
    def env_name(self):
        return self._env_name

    @property
    def stats(self):
        return self._stats

    def exec_command(self, *args, **kwargs) -> Tuple[int, str, str]:
        raise NotImplementedError

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

    def add_credentials_to_build_repos(self, repositories: List[dict]) -> List[dict]:
        modified_repositories = []
        for repo in repositories:
            if '-br' not in repo['name']:
                modified_repositories.append(repo)
                continue
            # Sometimes URL can start from 'deb' part, in this case we need to
            # get the actual URL from it and then add the rest of it back
            url_parts = None
            parsed_url_index = None
            repo_url = repo['url']
            if repo['url'].startswith(('deb ', 'deb-src ')):
                url_parts = repo['url'].split(' ')
                for i, part in enumerate(url_parts):
                    if part.startswith(('http', 'https')):
                        repo_url = part
                        parsed_url_index = i
                        break

            parsed = urllib.parse.urlparse(repo_url)
            self._logger.debug('Parsed repo url: %s', parsed)
            netloc = parsed.netloc
            if CONFIG.bs_token and '@' not in netloc:
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
                    and not url_parts):
                url = f'deb {url} ./'
            elif url_parts and parsed_url_index:
                url_parts[parsed_url_index] = url
                url = ' '.join(url_parts)
            self._logger.debug('Modified repo url: %s', url)
            repo['url'] = url
            modified_repositories.append(repo)
        self._logger.info('Repositories: %s', modified_repositories)
        return modified_repositories

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
        self._repositories = None
        if self._logger.hasHandlers():
            for handler in self._logger.handlers:
                handler.flush()

    def _render_template(self, template_name, result_file_path, **kwargs):
        template = self._template_lookup.get_template(template_name)
        with open(result_file_path, 'wt') as f:
            content = template.render(**kwargs)
            f.write(content)

    def _create_ansible_inventory_file(self, **kwargs):
        self._inventory_file_path = os.path.join(
            self._work_dir,
            self.ANSIBLE_INVENTORY_FILE,
        )
        self._render_template(
            f'{self.ANSIBLE_INVENTORY_FILE}.tmpl',
            self._inventory_file_path,
            env_name=self.env_name,
            connection_type=self.ansible_connection_type,
            **kwargs,
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
        package_epoch: Optional[int] = None,
    ) -> str:
        full_pkg_name = package_name
        delimiter = '='
        if package_version:
            delimiter = '='
            if self.pkg_manager in ('yum', 'dnf'):
                delimiter = '-'
            full_pkg_name = f'{package_name}{delimiter}{package_version}'
        if package_epoch:
            if (
                self.dist_name in CONFIG.rhel_flavors
                and self.dist_version in ('8', '9', '10')
                and package_version
            ):
                full_pkg_name = (f'{package_name}{delimiter}{package_epoch}:'
                                 f'{package_version}')
        return full_pkg_name

    def run_ansible_command(
        self, args: Union[tuple, list], retcode_none: bool = False,
        timeout: int = CONFIG.provision_timeout
    ):
        run_kwargs = {
            'args': args,
            'timeout': timeout
        }
        if retcode_none:
            run_kwargs['retcode'] = None
        cmd = local[self.ansible_playbook_binary].with_cwd(self._work_dir)
        formulated_cmd = cmd.formulate(args=run_kwargs.get('args', ()))
        exception_happened = False
        cmd_pid = None
        try:
            future = cmd.run_bg(**run_kwargs)
            cmd_pid = future.proc.pid
            future.wait()
            exit_code, stdout, stderr = future.returncode, future.stdout, future.stderr
        except ProcessExecutionError as e:
            stdout = e.stdout
            stderr = e.stderr
            exit_code = e.retcode
            exception_happened = True
        except ProcessTimedOut:
            stdout = ''
            stderr = f'Timeout occurred when running ansible command: "{formulated_cmd}"'
            exit_code = COMMAND_TIMEOUT_EXIT_CODE
            exception_happened = True
        except Exception as e:
            self._logger.error(
                'Unknown error happened during %s execution: %s',
                formulated_cmd
            )
            stdout = ''
            stderr = str(e)
            exit_code = 255

        if exception_happened and cmd_pid:
            try:
                os.killpg(cmd_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        return exit_code, stdout, stderr

    # First step
    def prepare_work_dir_files(self):
        # In case if you've removed worker folder, recreate one
        if not self._work_dir or not os.path.exists(self._work_dir):
            self._work_dir = self._create_work_dir()
            self._artifacts_dir = self._create_artifacts_dir()
        try:
            # Write resources that are not templated into working directory
            for ansible_file in self.ANSIBLE_FILES:
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
        }

    def __terraform_init(self):
        with FileLock(TF_INIT_LOCK_PATH, timeout=60, thread_local=False):
            return local['terraform'].with_env(TF_LOG='TRACE').with_cwd(self._work_dir).run(
                ('init', '-no-color'),
                timeout=CONFIG.provision_timeout,
            )

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
        return local['terraform'].with_env(TF_LOG='TRACE').with_cwd(self._work_dir).run(
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
            'package_proxy': CONFIG.package_proxy,
        }
        dist_major_version = self.dist_version[0]
        if self.dist_name in CONFIG.rhel_flavors and dist_major_version in ('6', '7'):
            epel_release_url = CONFIG.epel_release_urls.get(dist_major_version)
            if epel_release_url:
                var_dict['epel_release_url'] = epel_release_url
        if CONFIG.centos_baseurl:
            var_dict['centos_repo_baseurl'] = CONFIG.centos_baseurl
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
        exit_code, out, err = self.run_ansible_command(
            cmd_args, timeout=CONFIG.provision_timeout
        )
        if exit_code == COMMAND_TIMEOUT_EXIT_CODE:
            return 1, '', f'Provision has timed out: {out}\n{err}'
        elif exit_code != 0:
            return 1, '', f'Provision exited abnormally: {out}\n{err}'
        return exit_code, out, err

    def get_system_info_commands_list(self) -> Dict[str, tuple]:
        self._logger.debug('Returning default system info commands list')
        basic_commands = BASE_SYSTEM_INFO_COMMANDS.copy()
        if self._dist_name in CONFIG.rhel_flavors:
            basic_commands['Installed packages'] = ('rpm', '-qa')
            basic_commands['Repositories list'] = (
                 self.pkg_manager, 'repolist'
            )
            basic_commands['Repositories details'] = (
                'find', '/etc/yum.repos.d/', '-type', 'f',
                '-exec', 'cat', '{}', '+'
            )
        else:
            basic_commands['Installed packages'] = ('dpkg', '-l')
            basic_commands['Repositories list'] = ('apt-cache', 'policy')
            basic_commands['Repositories details'] = (
                'find', '/etc/apt/', '-type', 'f', '-name', '*.list*',
                '-o', '-name', '*.sources*', '-exec', 'cat', '{}', '+'
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
            start = datetime.datetime.utcnow()
            self._logger.info(
                'Running "%s" for env %s',
                cmd, self.env_name
            )
            try:
                binary, *args = cmd
                result = CommandExecutor(binary, **executor_params).run(args)
                output = '\n'.join([result.stdout, result.stderr])
                if result.is_successful():
                    successful_commands[section] = output
                else:
                    errored_commands[section] = output
            except Exception as e:
                errored_commands[section] = str(e)
            finish = datetime.datetime.utcnow()
            self._logger.info(
                '"%s" for env %s took %s',
                cmd, self.env_name, str(finish - start)
            )
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

    def install_package_no_log(
        self,
        package_name: str,
        package_version: Optional[str] = None,
        package_epoch: Optional[int] = None,
        module_name: Optional[str] = None,
        module_stream: Optional[str] = None,
        module_version: Optional[str] = None,
        semi_verbose: bool = False,
        verbose: bool = False,
        allow_fail: bool = False,
    ) -> Tuple[int, str, str]:
        full_pkg_name = self._detect_full_package_name(
            package_name,
            package_version=package_version,
            package_epoch=package_epoch,
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
        verbosity = ''
        if semi_verbose:
            verbosity = '-vv'
        if verbose:
            verbosity = '-vvvv'
        if verbosity:
            cmd_args.append(verbosity)
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )
        exit_code, out, err = self.run_ansible_command(
            cmd_args, timeout=CONFIG.provision_timeout
        )
        if exit_code == COMMAND_TIMEOUT_EXIT_CODE:
            self._logger.error(
                'Package was not installed due to command timeout: %s',
                f'{out}\n{err}'
            )
        if allow_fail and exit_code != 0:
            exit_code = 0
        if exit_code != 0:
            self._logger.error('Cannot install package %s: %s', full_pkg_name, err)
        return exit_code, out, err

    @command_decorator(
        'install_package',
        'Cannot install package',
        exception_class=InstallPackageError,
    )
    def install_package(
        self,
        package_name: str,
        package_version: Optional[str] = None,
        package_epoch: Optional[int] = None,
        module_name: Optional[str] = None,
        module_stream: Optional[str] = None,
        module_version: Optional[str] = None,
        semi_verbose: bool = False,
        verbose: bool = False,
        allow_fail: bool = False,
    ):
        return self.install_package_no_log(
            package_name,
            package_version=package_version,
            package_epoch=package_epoch,
            module_name=module_name,
            module_stream=module_stream,
            module_version=module_version,
            semi_verbose=semi_verbose,
            verbose=verbose,
            allow_fail=allow_fail,
        )

    def detect_protected_packages(self):
        if self.dist_name not in CONFIG.rhel_flavors:
            return []
        exit_code, stdout, stderr = self.exec_command(
            'ls', f'/etc/{self.pkg_manager}/protected.d/'
        )
        if exit_code != 0:
            return []
        files = [i.strip() for i in stdout.split('\n') if i.strip()]
        protected = []
        for file_ in files:
            exit_code, stdout, stderr = self.exec_command(
                'cat', f'/etc/{self.pkg_manager}/protected.d/{file_}',
            )
            if exit_code != 0:
                continue
            file_protected = [i.strip() for i in stdout.split('\n') if i.strip()]
            if file_protected:
                protected.extend(file_protected)
        protected.append('kernel-core')
        dnf_command = (
            r'dnf', '-q', '--qf=%{NAME}', 'repoquery', '--requires', '--resolve', '--recursive',
            *protected
        )
        exit_code, stdout, stderr = self.exec_command(*dnf_command)
        if exit_code != 0:
            self._logger.warning(
                'Cannot resolve non-uninstallable packages via DNF: %s',
                dnf_command
            )
            return protected
        dnf_protected = [i.strip() for i in stdout.split('\n') if i.strip()]
        if dnf_protected:
            protected.extend(dnf_protected)
        return list(set(protected))

    def _uninstall_package(
        self,
        package_name: str,
        package_version: Optional[str] = None,
    ):
        if package_name in CONFIG.uninstall_excluded_pkgs:
            return 0, '', ''

        if package_name in self.detect_protected_packages():
            return 0, f'Package {package_name} is protected', ''

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
        cmd_args.extend(['-t', 'uninstall_package'])
        cmd_args_str = ' '.join(cmd_args)
        self._logger.debug(
            'Running "ansible-playbook %s" command',
            cmd_args_str,
        )

        exit_code, out, err = self.run_ansible_command(
            cmd_args, timeout=CONFIG.provision_timeout
        )
        if exit_code == COMMAND_TIMEOUT_EXIT_CODE:
            self._logger.error(
                'Package was not uninstalled due to command timeout: %s',
                f'{out}\n{err}'
            )
        elif exit_code != 0:
            self._logger.error('Cannot uninstall package %s: %s', full_pkg_name, err)
        return exit_code, out, err

    def ensure_package_is_uninstalled(self, package_name: str):
        package_exists = self.check_package_existence(package_name)
        if package_exists:
            self._uninstall_package(package_name)

    @command_decorator(
        'uninstall_package',
        'Cannot uninstall package',
        exception_class=UninstallPackageError,
    )
    def uninstall_package(self, package_name: str):
        return self._uninstall_package(package_name)

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
        self._logger.info('Cloning %s to %s', repo_url, self._work_dir)
        repo_name = os.path.basename(repo_url)
        if not repo_name.endswith('.git'):
            repo_name += '.git'
        repo_reference_dir = None
        if CONFIG.git_reference_directory:
            repo_reference_dir = os.path.join(
                CONFIG.git_reference_directory, repo_name)
        repo_path = None
        for attempt in range(1, 6):
            try:
                repo_path = func(
                    repo_url,
                    git_ref,
                    self._work_dir,
                    self._logger,
                    reference_directory=repo_reference_dir
                )
            except (ProcessExecutionError, ProcessTimedOut):
                pass
            if not repo_path:
                self._logger.warning(
                    'Attempt %d to clone %s locally has failed',
                    attempt, repo_url
                )
                self._logger.debug('Sleeping before making another attempt')
                time.sleep(random.randint(5, 10))
            else:
                break
        return repo_path

    def run_third_party_test(
        self,
        executor: Union[AnsibleExecutor, BatsExecutor, CommandExecutor, ShellExecutor],
        cmd_args: List[str],
        docker_args: Optional[List[str]] = None,
        workdir: str = '',
        artifacts_key: str = '',
        additional_section_name: str = '',
        env_vars: Optional[List[str]] = None,
    ):
        raise NotImplementedError

    def check_package_existence(
        self,
        package_name: str,
        package_version: Optional[str] = None,
    ) -> bool:
        if self.dist_name in CONFIG.rhel_flavors:
            cmd = ('rpm', '-q', package_name)
        elif self.dist_name in CONFIG.debian_flavors:
            cmd = ('dpkg-query', '-Wf', r'${db:Status-Status} ${Package}\n',
                   package_name)
        else:
            raise ValueError(f'Unknown distribution: {self.dist_name}')
        exit_code, stdout, stderr = self.exec_command(*cmd)
        installed = exit_code == 0
        if installed and package_version:
            return package_version in stdout
        return installed

    def ensure_package_is_installed(
        self,
        package_name: str,
        package_version: Optional[str] = None,
        package_epoch: Optional[int] = None,
    ):
        package_installed = self.check_package_existence(
            package_name,
            package_version=package_version,
        )
        if not package_installed:
            self.install_package_no_log(
                package_name,
                package_version=package_version,
                package_epoch=package_epoch,
                semi_verbose=True
            )

    def get_init_script(self, tests_dir: Path) -> Optional[Path]:
        init = None
        for test in tests_dir.iterdir():
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
        return init

    def find_tests(self, tests_dir: str) -> List[Path]:
        self._logger.info('Looking tests on the remote in %s', tests_dir)
        if not tests_dir.endswith('/'):
            tests_dir += '/'
        _, stdout, _ = self.exec_command(
            'find', tests_dir, '-maxdepth', '1', '-type', 'f', '-o', '-type', 'l'
        )
        tests_list = [Path(i) for i in stdout.split('\n')]
        self._logger.debug('Tests list: %s', tests_list)
        tests_list.sort()
        organized_tests_list = []
        install = None
        for test in tests_list:
            if test.is_dir() or test.name in self.INIT_TESTS:
                continue
            if test.name == '0_install':
                install = test
                continue
            else:
                organized_tests_list.append(test)
        # 0_init is executed elsewhere, 0_install should be the first here
        if install:
            organized_tests_list.insert(0, install)
        return organized_tests_list

    def detect_executor(self, test_path: str) -> DetectExecutorResult:
        extension = Path(test_path).suffix
        if extension in EXECUTORS_MAPPING:
            return EXECUTORS_MAPPING[extension]
        # Try to detect file format with magic
        _, magic_out, _ = self.exec_command('file', test_path)
        if 'symbolic link' in magic_out:
            target_file = magic_out.split(' ')[-1].strip('\n`\'"')
            new_path = os.path.join(os.path.dirname(test_path), target_file)
            _, magic_out, _ = self.exec_command('file', new_path)
        if 'directory' in magic_out:
            self._logger.info("Skipping %s since it's a directory", test_path)
            return
        for regex, executor_class_ in FILE_TYPE_REGEXES_MAPPING.items():
            if re.search(regex, magic_out, re.IGNORECASE):
                return executor_class_  # noqa
        return ShellExecutor  # noqa

    def detect_python_binary(
        self,
        test_path: Union[Path, str]
    ) -> Tuple[str, str]:
        default_python = 'python3'
        if (self.dist_name in CONFIG.rhel_flavors
                and self.dist_version.startswith(('6', '7'))):
            default_python = 'python'
        with open(test_path, 'rt') as f:
            shebang = f.readline()
            result = INTERPRETER_REGEX.search(shebang)
            if not result:
                return default_python, ''
            result_dict = result.groupdict()
            if 'python_interpreter' not in result_dict:
                return default_python, ''
            interpreter = result_dict['python_interpreter']
            options = ''
            if 'options' in result_dict:
                options = result_dict['options'].strip()
            return interpreter, options

    def _run_test_file(
        self,
        test_file: Path,
        remote_workdir: str,
        local_workdir: str,
        executors_cache: dict,
        local_tests_path: Path,
    ) -> List[str]:
        executor_params = self.get_test_executor_params()
        executor_params['timeout'] = CONFIG.tests_exec_timeout
        workdir = remote_workdir
        errors = []
        executor_class = self.detect_executor(
            os.path.join(remote_workdir, test_file.name)
        )
        if not executor_class:
            self._logger.warning(
                'Cannot get executor for test %s',
                test_file
            )
            return errors
        self._logger.info('Running %s', test_file)
        self._logger.debug(
            'Executor: %s', executor_class.__name__
        )
        if executor_class == AnsibleExecutor:
            cmd_args = [test_file]
            workdir = local_workdir
            executor_params['binary_name'] = self.ansible_playbook_binary
        else:
            cmd_args = [test_file.name]
            executor_params.pop('binary_name', None)
        if executor_class == CommandExecutor:
            local_file_location = local_tests_path / test_file.name
            python, options = self.detect_python_binary(local_file_location)
            if options:
                cmd_args.insert(0, options)
            executor = CommandExecutor(python, **executor_params)
        else:
            if executor_class in executors_cache:
                executor = executors_cache[executor_class]
            else:
                executor: Union[
                    AnsibleExecutor,
                    BatsExecutor,
                    CommandExecutor,
                    ShellExecutor,
                ] = executor_class(**executor_params)
                if executor_class != CommandExecutor:
                    executors_cache[executor_class] = executor
        self._logger.debug(
            'Running the third party test %s on %s...',
            test_file.name,
            self.env_name,
        )
        try:
            key = f'{THIRD_PARTY_SECTION_NAME}_test_{test_file.name}'
            self.run_third_party_test(
                executor=executor,
                cmd_args=cmd_args,
                docker_args=['--workdir', workdir],
                workdir=workdir,
                artifacts_key=key,
                additional_section_name=THIRD_PARTY_SECTION_NAME,
                env_vars=self._test_env.get('extra_env_vars', []),
            )
        except ThirdPartyTestError:
            errors.append(f'Test {test_file.name} has failed')
        except Exception:
            errors.append(
                f'Test {test_file.name} failed:\n{traceback.format_exc()}'
            )
            self._logger.exception(
                'An unknown error occurred during the test execution'
            )
        return errors

    @command_decorator(
        f'{THIRD_PARTY_SECTION_NAME}_tests_wrapper',
        'Preparation/running third party tests has failed',
        ThirdPartyTestError,
    )
    def run_third_party_tests(
        self,
        package_name: str,
        package_version: Optional[str] = None,
        package_epoch: Optional[int] = None,
    ):
        if not self._test_configuration:
            return 0, 'Nothing to run', ''
        errors = []
        executors_cache = {}

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
            remote_workdir = os.path.join(
                CONFIG.tests_base_dir,
                test_repo_path.name,
                test_dir,
            )
            local_workdir = self._work_dir
            tests_path = Path(test_repo_path, test_dir)
            if not tests_path.exists():
                self._logger.warning('Directory %s does not exist', tests_path)
                self._logger.warning('Skipping test configuration')
                continue
            init_test = self.get_init_script(tests_path)
            if init_test:
                self._run_test_file(
                    init_test,
                    remote_workdir,
                    local_workdir,
                    executors_cache,
                    tests_path,
                )
            tests_list = self.find_tests(remote_workdir)
            # Check if package has 0_init-like script
            for test_file in tests_list:
                if tests_to_run and test_file.name not in tests_to_run:
                    continue
                if (('0_init' not in test_file.name
                     or '0_install' not in test_file.name)):
                    self.ensure_package_is_installed(
                        package_name,
                        package_version=package_version,
                        package_epoch=package_epoch,
                    )
                test_errors = self._run_test_file(
                    test_file,
                    remote_workdir,
                    local_workdir,
                    executors_cache,
                    tests_path,
                )
                if test_errors:
                    errors.extend(test_errors)
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
            return re.sub(r'\[local\]', f'[{self._task_id}]', log_string)

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

    def _stop_env(self):
        if not os.path.exists(self._work_dir):
            return 0, '', f'Working directory {self._work_dir} does not exist'
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
        return local['terraform'].with_env(TF_LOG='TRACE').with_cwd(self._work_dir).run(
            args=cmd_args,
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )

    # After: install_package and run_tests
    @command_decorator(
        'stop_environment',
        'Cannot destroy environment',
        exception_class=StopEnvironmentError,
        is_abortable=False,
    )
    def stop_env(self):
        return self._stop_env()

    def erase_work_dir(self):
        if not self._work_dir:
            return
        if self._work_dir and not os.path.exists(self._work_dir):
            return
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
            verbose=verbose,
        )
        self._tests_dir = CONFIG.tests_base_dir
        self._ssh_client: Optional[Union[AsyncSSHClient, LongRunSSHClient]] = None
        self._vm_ip = None
        self._vm_alive = vm_alive

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

    @property
    def vm_ip(self):
        return self._vm_ip

    @property
    def default_ssh_params(self) -> Dict[str, Any]:
        max_keepalive_msgs = (
            CONFIG.task_soft_time_limit // CONFIG.keepalive_interval
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

    def get_test_executor_params(self) -> dict:
        params = super().get_test_executor_params()
        params['ssh_client'] = self._ssh_client
        return params

    @command_decorator(
        'start_environment',
        'Cannot start environment',
        exception_class=StartEnvironmentError,
        is_abortable=False,
    )
    def start_env(self):
        exit_code, stdout, stderr = super().start_env()
        # VM gets its IP address only after deploy.
        # To extract it, the `vm_ip` output should be defined
        # in Terraform main file.
        ip_exit_code, ip_stdout, ip_stderr = local['terraform'].with_env(TF_LOG='TRACE').with_cwd(
            self._work_dir).run(
            args=('output', '-raw',  '-no-color', 'vm_ip'),
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )
        if ip_exit_code != 0:
            error_message = f'Cannot get VM IP: {ip_stderr}'
            self._logger.error(error_message)
            return ip_exit_code, ip_stdout, ip_stderr
        self._vm_ip = ip_stdout
        # Because we don't know about a VM's IP before its creating
        # And get an IP after launch of terraform script
        self._create_ansible_inventory_file(vm_ip=self._vm_ip)
        self._logger.info('Waiting for SSH port to be available')
        ssh_exit_code, ssh_stdout, ssh_stderr = self._wait_for_ssh()
        if ssh_exit_code:
            self._logger.error(
                'Machine %s is started, but SSH connection is not working',
                self.env_name,
            )
        else:
            self._logger.info('Machine is available for SSH connection')
        final_exit_code = exit_code or ssh_exit_code
        final_stdout = f'{stdout}\n\n{ssh_stdout}'
        final_stderr = f'{stderr}\n\n{ssh_stderr}'
        return final_exit_code, final_stdout, final_stderr

    def setup(self, skip_provision: bool = False):
        super().setup(skip_provision=skip_provision)
        params = self.default_ssh_params
        params['timeout'] = CONFIG.provision_timeout
        self._ssh_client = LongRunSSHClient(**params)

    def teardown(self, publish_artifacts: bool = True):
        if not self._vm_alive:
            if self._ssh_client:
                try:
                    self._ssh_client.close()
                except:
                    pass
            super().teardown(publish_artifacts=publish_artifacts)

    def exec_command(self, *args, **kwargs) -> Tuple[int, str, str]:
        command = ' '.join(args)
        result = self._ssh_client.sync_run_command(command)
        return result.exit_code, result.stdout, result.stderr

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
            result = None
            for attempt in range(1, 6):
                cmd = (f'if [ -e {repo_path} ]; then cd {repo_path} && '
                       f'git reset --hard origin/master && git checkout master && git pull; '
                       f'else cd {self._tests_dir} && git clone {repo_url}; fi')
                result = self._ssh_client.sync_run_command(cmd)
                if result.is_successful():
                    break
                self._logger.warning(
                    'Attempt to clone repository on VM failed:\n%s\n%s',
                    result.stdout, result.stderr,
                )
                time.sleep(random.randint(5, 10))
            if not result or (result and not result.is_successful()):
                return

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
            executor: Union[AnsibleExecutor, BatsExecutor, CommandExecutor, ShellExecutor],
            cmd_args: List[str],
            docker_args: Optional[List[str]] = None,
            workdir: str = '',
            artifacts_key: str = '',
            additional_section_name: str = '',
            env_vars: Optional[List[str]] = None,
    ):
        result = executor.run(
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
