# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System docker environment runner."""

import os
from pathlib import Path
from typing import (
    Callable,
    Dict,
    List,
    Optional,
    Union,
    Tuple,
)

from plumbum import local

from alts.shared.exceptions import (
    PackageIntegrityTestsError,
    ProvisionError,
    ThirdPartyTestError,
)
from alts.shared.uploaders.base import BaseLogsUploader
from alts.worker import CONFIG
from alts.worker.executors.ansible import AnsibleExecutor
from alts.worker.executors.bats import BatsExecutor
from alts.worker.executors.command import CommandExecutor
from alts.worker.executors.shell import ShellExecutor
from alts.worker.runners.base import (
    TESTS_SECTION_NAME,
    BaseRunner,
    command_decorator,
)

__all__ = ['DockerRunner']


ARCH_PLATFORM_MAPPING = {
    'i386': 'linux/386',
    'i486': 'linux/386',
    'i586': 'linux/386',
    'i686': 'linux/386',
    'amd64': 'linux/amd64',
    'x86_64': 'linux/amd64',
    'arm64': 'linux/arm64/v8',
    'aarch64': 'linux/arm64/v8',
    'ppc64le': 'linux/ppc64le',
    's390x': 'linux/s390x',
}


class DockerRunner(BaseRunner):
    """Docker environment runner for testing tasks."""

    TYPE = 'docker'
    TF_MAIN_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'

    def __init__(
        self,
        task_id: str,
        task_is_aborted: Callable,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        test_configuration: Optional[dict] = None,
        test_flavor: Optional[Dict[str, str]] = None,
        vm_alive: bool = False,
        artifacts_uploader: Optional[BaseLogsUploader] = None,
        package_channel: Optional[str] = None,
        verbose: bool = False,
    ):
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
        super().__init__(
            task_id,
            task_is_aborted,
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            test_configuration=test_configuration,
            test_flavor=test_flavor,
            vm_alive=vm_alive,
            artifacts_uploader=artifacts_uploader,
            package_channel=package_channel,
            verbose=verbose,
        )
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
        self._renderer.render_tf_main_file(
            self.dist_name,
            self.dist_version,
            self.dist_arch,
            self.env_name
        )

    def _render_tf_variables_file(self):
        pass

    def exec_command(
        self,
        *args,
        workdir: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """
        Executes a command inside docker container.

        Parameters
        ----------
        args : tuple
            Arguments to create a command and its arguments to execute
            inside the container.
        workdir : str, optional
            Working directory

        Returns
        -------
        tuple
            Executed command exit code, standard output and standard error.
        """
        cmd = ['exec']
        if workdir:
            cmd.extend(['--workdir', workdir])
        cmd.extend([str(self.env_name), *args])
        self._logger.debug(
            'Running "docker %s" command',
            ' '.join(cmd),
        )
        return local['docker'].with_cwd(self._work_dir).run(
            args=tuple(cmd),
            retcode=None,
        )

    @staticmethod
    def copy(copy_args: List[str]):
        """
        Copies file/dir into docker container.
        """
        local['docker'].run(
            ['cp', *copy_args],
            retcode=None,
        )

    def replace_mirrors_for_debian_sources(self):
        sources_file = '/etc/apt/sources.list'
        replacements_dict = {}
        if self.dist_name == 'debian':
            replacements_dict = CONFIG.debian_mirror_replacements
            # Debian 9 has its repos in archive now + the archive
            # does not contain updates repository, so hacking sources.list
            if self.dist_version.startswith('9'):
                for pattern in (
                    r's/.*(stretch-updates).*//',
                    r's/(deb|security)\.debian\.org/archive\.debian\.org/',
                ):
                    self.exec_command(
                        'sed',
                        '-E',
                        '-i',
                        pattern,
                        sources_file,
                    )

            if self.dist_version.startswith('12'):
                sources_file = '/etc/apt/sources.list.d/debian.sources'
                self.exec_command('sed', '-i', 's|Signed-By.*||g', sources_file)
            self.exec_command(
                'sed',
                '-E',
                '-i',
                r's/(debian|debian-security)(\s|$)/\1\/\2/g',
                sources_file,
            )
        elif self.dist_name == 'ubuntu':
            replacements_dict = CONFIG.ubuntu_mirror_replacements
            if self.dist_version.startswith('24'):
                sources_file = '/etc/apt/sources.list.d/ubuntu.sources'
        for pattern, repl in replacements_dict.items():
            if not repl:
                continue
            self.exec_command(
                'sed',
                '-i',
                f's|{pattern}|{repl}|g',
                sources_file,
            )

    @command_decorator(
        'initial_provision',
        'Cannot run initial provision',
        exception_class=ProvisionError,
    )
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
        # This is needed because Debian/Ubuntu docker images
        # may not have python as pre-installed package
        if self._dist_name in CONFIG.debian_flavors:
            self.replace_mirrors_for_debian_sources()
            self._logger.info('Installing python3 package...')
            exit_code, stdout, stderr = self.exec_command(
                self.pkg_manager, 'update',
            )
            if exit_code != 0:
                return exit_code, stdout, stderr
            cmd_args = (self.pkg_manager, 'install', '-y', 'python3')
            exit_code, stdout, stderr = self.exec_command(*cmd_args)
            if exit_code != 0:
                return exit_code, stdout, stderr
            self._logger.info('Installation is completed')
        if self.dist_name in CONFIG.rhel_flavors and self.dist_version == '6':
            self._logger.info('Removing old repositories')
            self.exec_command(
                'find', '/etc/yum.repos.d', '-type', 'f', '-exec',
                'rm', '-f', '{}', '+',
            )
        return super().initial_provision(verbose=verbose)

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
        package_version:    str
            Package version

        Returns
        -------
        tuple
            Exit code, stdout and stderr from executed command

        """
        tests_dir_basename = os.path.basename(self._integrity_tests_dir)
        remote_tests_path = os.path.join(
            CONFIG.tests_base_dir,
            tests_dir_basename,
        )
        cmd_args = ['py.test', '--tap-stream', '--package-name', package_name]
        if package_version:
            full_pkg_name = f'{package_name}-{package_version}'
            cmd_args.extend(['--package-version', package_version])
        else:
            full_pkg_name = package_name
        cmd_args.append('tests')
        self._logger.info(
            'Running package integrity tests for %s on %s...',
            full_pkg_name,
            self.env_name,
        )
        return self.exec_command(*cmd_args, workdir=remote_tests_path)

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
        return executor.run_docker_command(
            cmd_args=cmd_args,
            docker_args=docker_args,
            env_vars=env_vars,
        ).model_dump().values()

    def clone_third_party_repo(
        self,
        repo_url: str,
        git_ref: str,
    ) -> Optional[Path]:
        test_repo_path = super().clone_third_party_repo(repo_url, git_ref)
        if not test_repo_path:
            return
        self._logger.info('Copying tests to container')
        self._logger.debug('Repo path: %s', test_repo_path)
        self.exec_command('mkdir', '-p', CONFIG.tests_base_dir)
        self.copy([
            str(test_repo_path),
            f'{self.env_name}:{CONFIG.tests_base_dir}/{test_repo_path.name}',
        ])
        return test_repo_path

    def _stop_env(self):
        _, container_id, _ = local['terraform'].with_env(TF_LOG='TRACE').with_cwd(
            self._work_dir).run(
            args=('output', '-raw', '-no-color', 'container_id'),
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )
        exit_code, out, err = super()._stop_env()
        if exit_code != 0:
            return self.exec_command('rm', '-f', container_id)
        return exit_code, out, err
