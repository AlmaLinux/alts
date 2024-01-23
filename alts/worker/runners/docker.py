# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-13

"""AlmaLinux Test System docker environment runner."""

import os
from pathlib import Path
from typing import List, Optional, Union

from plumbum import local

from alts.shared.exceptions import (
    PackageIntegrityTestsError,
    ProvisionError,
    ThirdPartyTestError,
)
from alts.worker import CONFIG
from alts.worker.executors.ansible import AnsibleExecutor
from alts.worker.executors.bats import BatsExecutor
from alts.worker.executors.shell import ShellExecutor
from alts.worker.runners.base import (
    TESTS_SECTION_NAME,
    BaseRunner,
    command_decorator,
)

__all__ = ['DockerRunner']


class DockerRunner(BaseRunner):
    """Docker environment runner for testing tasks."""

    TYPE = 'docker'
    TF_MAIN_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'

    def __init__(
        self,
        task_id: str,
        dist_name: str,
        dist_version: Union[str, int],
        repositories: Optional[List[dict]] = None,
        dist_arch: str = 'x86_64',
        test_configuration: Optional[dict] = None,
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
            dist_name,
            dist_version,
            repositories=repositories,
            dist_arch=dist_arch,
            test_configuration=test_configuration,
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
        docker_tf_file = os.path.join(self._work_dir, self.TF_MAIN_FILE)
        image_name = f'{self.dist_name}:{self.dist_version}'
        external_network = os.environ.get('EXTERNAL_NETWORK', None)

        self._render_template(
            f'{self.TF_MAIN_FILE}.tmpl',
            docker_tf_file,
            dist_name=self.dist_name,
            image_name=image_name,
            container_name=self.env_name,
            external_network=external_network,
        )

    def _render_tf_variables_file(self):
        pass

    def _exec(
        self,
        cmd_with_args: Union[tuple, list],
        workdir: Optional[str] = None,
    ):
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
        cmd = ['exec']
        if workdir:
            cmd.extend(['--workdir', workdir])
        cmd.extend([str(self.env_name), *cmd_with_args])
        self._logger.debug(
            'Running "docker %s" command',
            ' '.join(cmd),
        )
        return local['docker'].run(
            args=tuple(cmd),
            retcode=None,
            cwd=self._work_dir,
        )

    def _copy(self, copy_args: List[str]):
        """
        Copies file/dir into docker container.
        """
        local['docker'].run(
            ['cp', *copy_args],
            retcode=None,
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
        if self._dist_name in CONFIG.debian_flavors:
            self._logger.info('Installing python3 package...')
            exit_code, stdout, stderr = self._exec(
                (self.pkg_manager, 'update'),
            )
            if exit_code != 0:
                return exit_code, stdout, stderr
            cmd_args = (self.pkg_manager, 'install', '-y', 'python3')
            exit_code, _, stderr = self._exec(cmd_args)
            if exit_code != 0:
                raise ProvisionError(
                    f'Cannot install package python3: {stderr}'
                )
            self._logger.info('Installation is completed')
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
        remote_tests_path = os.path.join('/tests', tests_dir_basename)
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
        return self._exec(cmd_args, workdir=remote_tests_path)

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
        return (
            executor.run_docker_command(
                cmd_args=cmd_args,
                docker_args=docker_args,
                env_vars=env_vars,
            )
            .model_dump()
            .values()
        )

    def clone_third_party_repo(
        self,
        repo_url: str,
        git_ref: str,
    ) -> Optional[Path]:
        test_repo_path = super().clone_third_party_repo(repo_url, git_ref)
        if not test_repo_path:
            return
        self._copy([
            str(test_repo_path),
            f'{self.env_name}:/tests/{test_repo_path.name}',
        ])
        return test_repo_path
