import logging
import os
import shutil
import tempfile
import uuid
from abc import abstractmethod
from pathlib import Path
from typing import Union

from plumbum import local, ProcessExecutionError

from alts.errors import (DestroyEnvironmentError, EnvironmentStartError, ProvisionError,
                         TerraformInitializationError, WorkDirPreparationError)
from alts.runners import TEMPLATE_LOOKUP, RESOURCES_DIRECTORY
from alts.utils import set_directory


class BaseRunner(object):
    VERSIONS_TF_FILE = 'versions.tf'
    ANSIBLE_PLAYBOOK = 'playbook.yml'
    ANSIBLE_INVENTORY_FILE = 'hosts'
    TERRAFORM_RESOURCES = [VERSIONS_TF_FILE, ANSIBLE_PLAYBOOK]
    TEMPFILE_PREFIX = 'base_test_runner_'

    def __init__(self, dist_name: str, dist_version: Union[str, int]):
        self._dist_name = dist_name
        self._dist_version = str(dist_version)
        self._env_id = uuid.uuid4()
        self._work_dir = self._create_work_dir()
        self._artifacts_dir = self._create_artifacts_dir()
        self._terraform = local['terraform']
        self._playbook_runner = local['ansible-playbook']
        self._ansible_connection_type = 'ssh'
        self._pkg_manager = 'yum'
        if dist_name in ('debian', 'ubuntu', 'raspbian'):
            self._pkg_manager = 'apt'

    # TODO: Think of better implementation
    def _create_work_dir(self):
        return Path(tempfile.mkdtemp(prefix=self.TEMPFILE_PREFIX))

    # TODO: Think of better implementation
    def _create_artifacts_dir(self):
        if not self._work_dir:
            self._work_dir = self._create_work_dir()
        path = self._work_dir / 'artifacts'
        os.mkdir(path)
        return path

    def __del__(self):
        self.destroy_env()
        self.erase_work_dir()

    # TODO: Introduce steps dependencies of some sort

    # First step
    def prepare_work_dir_files(self):
        try:
            env_name = str(self._env_id)
            hosts_group_name = f'test_group_{env_name}'
            # Process all templates first
            hosts_template = TEMPLATE_LOOKUP.get_template(f'{self.ANSIBLE_INVENTORY_FILE}.tmpl')
            with open(os.path.join(self._work_dir, self.ANSIBLE_INVENTORY_FILE), 'w') as f:
                f.write(hosts_template.render(hosts_group_name=hosts_group_name, env_name=env_name))
            # Write resources that are not templated into working directory
            for tf_file in self.TERRAFORM_RESOURCES:
                shutil.copy(os.path.join(RESOURCES_DIRECTORY, tf_file), os.path.join(self._work_dir, tf_file))
        except Exception as e:
            raise WorkDirPreparationError('Cannot create working directory and needed files') from e

    # After: prepare_work_dir_files
    def initialize_terraform(self):
        try:
            # Initialize Terraform environment in working directory
            with set_directory(self._work_dir):
                self._terraform('init')
        except ProcessExecutionError as e:
            raise TerraformInitializationError('Cannot initialize Terraform environment') from e

    # After: initialize_terraform
    def start_env(self):
        try:
            with set_directory(self._work_dir):
                self._terraform('apply', '--auto-approve')
        except ProcessExecutionError as e:
            raise EnvironmentStartError('Cannot start environment') from e

    # After: start_env
    def initial_provision(self):
        try:
            with set_directory(self._work_dir):
                # TODO: Capture command result into log
                result = self._playbook_runner('-c', self._ansible_connection_type, '-i', 'hosts',
                                               self.ANSIBLE_PLAYBOOK)
                print(result)
        except ProcessExecutionError as e:
            raise ProvisionError('Cannot provision environment') from e

    @abstractmethod
    def install_package(self, package_name: str):
        raise NotImplementedError('Should be implemented in subclasses')

    @abstractmethod
    def run_tests(self):
        # Run tests and collect their results
        raise NotImplementedError('Should be implemented in subclasses')

    @abstractmethod
    def gather_artifacts(self):
        # Should place all artifacts into artifacts directory
        raise NotImplementedError('Should be implemented in subclasses')

    @abstractmethod
    def publish_artifacts_to_storage(self):
        # Should upload artifacts from artifacts directory to preffered artifacts storage (S3, Minio, etc.)
        raise NotImplementedError('Should be implemented in subclasses')

    # After: install_package and run_tests
    def destroy_env(self):
        if os.path.exists(self._work_dir):
            try:
                with set_directory(self._work_dir):
                    self._terraform('destroy', '--auto-approve')
            except Exception as e:
                logging.warning(f'Exception during container destroy: {e}')
                raise DestroyEnvironmentError('Cannot destroy environment via Terraform') from e

    def erase_work_dir(self):
        if os.path.exists(self._work_dir):
            shutil.rmtree(self._work_dir)
