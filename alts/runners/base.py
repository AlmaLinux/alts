import logging
import os
import shutil
import tempfile
import uuid
from abc import abstractmethod
from functools import wraps
from pathlib import Path
from typing import List, Union

import boto3
from boto3.exceptions import S3UploadFailedError
from mako.lookup import TemplateLookup
from plumbum import local

from alts import config
from alts.errors import (ProvisionError, PublishArtifactsError,
                         StartEnvironmentError, StopEnvironmentError,
                         TerraformInitializationError,
                         WorkDirPreparationError)
from alts.utils import set_directory


__all__ = ['BaseRunner', 'command_decorator', 'RESOURCES_DIRECTORY',
           'TEMPLATE_LOOKUP']


RESOURCES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'resources')
TEMPLATE_LOOKUP = TemplateLookup(directories=[RESOURCES_DIRECTORY])


def command_decorator(exception_class, artifacts_key, error_message):
    def method_wrapper(fn):
        @wraps(fn)
        def inner_wrapper(*args, **kwargs):
            self = args[0]
            args = args[1:]
            if not os.path.exists(self._work_dir):
                return
            with set_directory(self._work_dir):
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
        return inner_wrapper
    return method_wrapper


class BaseRunner(object):
    VERSIONS_TF_FILE = 'versions.tf'
    ANSIBLE_PLAYBOOK = 'playbook.yml'
    ANSIBLE_INVENTORY_FILE = 'hosts'
    TERRAFORM_RESOURCES = [VERSIONS_TF_FILE, ANSIBLE_PLAYBOOK]
    TEMPFILE_PREFIX = 'base_test_runner_'

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int],
                 repositories: List[dict]):
        # Environment ID and working directory preparation
        self._task_id = task_id
        self._env_id = uuid.uuid4()
        self._logger = logging.getLogger(__file__)
        self._work_dir = self._create_work_dir()
        self._artifacts_dir = self._create_artifacts_dir()

        # Basic commands and tools setup
        self._terraform = local['terraform']
        self._playbook_runner = local['ansible-playbook']
        self._ansible_connection_type = 'ssh'
        self._pkg_manager = 'yum'
        if dist_name in ('debian', 'ubuntu', 'raspbian'):
            self._pkg_manager = 'apt'

        # Package-specific variables that define needed container/VM
        self._dist_name = dist_name
        self._dist_version = str(dist_version)

        # Package installation and test stuff
        self._repositories = repositories

        self._artifacts = {}

    @property
    def artifacts(self):
        return self._artifacts

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
        self.stop_env()
        self.erase_work_dir()

    # TODO: Introduce steps dependencies of some sort

    # First step
    def prepare_work_dir_files(self):
        # In case if you've removed worker folder, recreate one
        if not os.path.exists(self._work_dir):
            self._work_dir = self._create_work_dir()
            self._artifacts_dir = self._create_artifacts_dir()
        try:
            env_name = str(self._env_id)
            hosts_group_name = f'test_group_{env_name}'
            # Process all templates first
            hosts_template = TEMPLATE_LOOKUP.get_template(
                f'{self.ANSIBLE_INVENTORY_FILE}.tmpl')
            inventory_file_path = os.path.join(self._work_dir,
                                               self.ANSIBLE_INVENTORY_FILE)
            with open(inventory_file_path, 'w') as f:
                f.write(hosts_template.render(
                    hosts_group_name=hosts_group_name, env_name=env_name))
            # Write resources that are not templated into working directory
            for tf_file in self.TERRAFORM_RESOURCES:
                shutil.copy(os.path.join(RESOURCES_DIRECTORY, tf_file),
                            os.path.join(self._work_dir, tf_file))
        except Exception as e:
            raise WorkDirPreparationError('Cannot create working directory and'
                                          ' needed files') from e

    # After: prepare_work_dir_files
    @command_decorator(TerraformInitializationError, 'initialize_terraform',
                       'Cannot initialize terraform')
    def initialize_terraform(self):
        self._logger.info('Initializing Terraform environment...')
        return self._terraform.run('init', retcode=None)

    # After: initialize_terraform
    @command_decorator(StartEnvironmentError, 'start_environment',
                       'Cannot start environment')
    def start_env(self):
        self._logger.info('Starting the environment...')
        return self._terraform.run(args=('apply', '--auto-approve'),
                                   retcode=None)

    # After: start_env
    @command_decorator(ProvisionError, 'initial_provision',
                       'Cannot run initial provision')
    def initial_provision(self):
        cmd_args = ('-c', self._ansible_connection_type, '-i', 'hosts',
                    self.ANSIBLE_PLAYBOOK, '--extra-vars',
                    f'repositories={self._repositories}')
        self._logger.info('Provisioning the environment...')
        return self._playbook_runner.run(args=cmd_args, retcode=None)

    @abstractmethod
    def install_package(self, package_name: str):
        raise NotImplementedError('Should be implemented in subclasses')

    def publish_artifacts_to_storage(self):
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
            's3', region_name=config.s3_region,
            aws_access_key_id=config.s3_access_key_id,
            aws_secret_access_key=config.s3_secret_access_key
        )
        error_when_uploading = False
        for artifact in os.listdir(self._artifacts_dir):
            artifact_path = os.path.join(self._artifacts_dir, artifact)
            object_name = os.path.join(config.ARTIFACTS_ROOT_DIRECTORY,
                                       self._task_id, artifact)
            try:
                self._logger.info(f'Uploading artifact {artifact_path} to S3')
                client.upload_file(artifact_path, config.s3_bucket, object_name)
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
        if os.path.exists(self._work_dir):
            self._logger.info('Destroying the environment...')
            return self._terraform.run(args=('destroy', '--auto-approve'),
                                       retcode=None)

    def erase_work_dir(self):
        if os.path.exists(self._work_dir):
            self._logger.info('Erasing working directory...')
            try:
                shutil.rmtree(self._work_dir)
            except Exception as e:
                self._logger.error(f'Error while erasing working directory: {e}')
            else:
                self._logger.info('Working directory was successfully removed')

    def setup(self):
        self.prepare_work_dir_files()
        self.initialize_terraform()
        self.start_env()
        self.initial_provision()

    def teardown(self):
        self.stop_env()
        self.publish_artifacts_to_storage()
        self.erase_work_dir()
