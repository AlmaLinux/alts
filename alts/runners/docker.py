import os
from typing import Union

from plumbum import local, ProcessExecutionError

from alts.errors import InstallPackageError, WorkDirPreparationError
from alts.runners import TEMPLATE_LOOKUP
from alts.runners.base import BaseRunner
from alts.utils import set_directory


class DockerRunner(BaseRunner):
    DOCKER_TF_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'

    def __init__(self, dist_name: str, dist_version: Union[str, int]):
        super().__init__(dist_name, dist_version)
        self._ansible_connection_type = 'docker'
        self._docker = local['docker']

    def prepare_work_dir_files(self):
        try:
            super().prepare_work_dir_files()
            env_name = str(self._env_id)
            docker_template = TEMPLATE_LOOKUP.get_template(f'{self.DOCKER_TF_FILE}.tmpl')
            with open(os.path.join(self._work_dir, self.DOCKER_TF_FILE), 'w') as f:
                f.write(docker_template.render(dist_name=self._dist_name, dist_version=self._dist_version,
                                               container_name=env_name))
        except WorkDirPreparationError:
            raise
        except Exception as e:
            raise WorkDirPreparationError('Cannot create working directory and needed files') from e

    def install_package(self, package_name: str):
        try:
            with set_directory(self._work_dir):
                # TODO: Capture command result into log
                result = self._docker('exec', str(self._env_id), self._pkg_manager, 'install', '-y', package_name)
                print(result)
        except ProcessExecutionError as e:
            raise InstallPackageError(f'Cannot install package {package_name}') from e
