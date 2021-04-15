import os
from typing import Union, List

from plumbum import local

from alts.errors import InstallPackageError, WorkDirPreparationError
from alts.runners.base import BaseRunner, command_decorator, TEMPLATE_LOOKUP


__all__ = ['DockerRunner']


class DockerRunner(BaseRunner):
    DOCKER_TF_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'

    def __init__(self, task_id: str, dist_name: str, dist_version: Union[str, int],
                 repositories: List[dict]):
        super().__init__(task_id, dist_name, dist_version, repositories)
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

    @command_decorator(InstallPackageError, 'install_package', 'Cannot install package')
    def install_package(self, package_name: str, package_version: str = None):
        cmd_args = ('exec', str(self._env_id), self._pkg_manager, 'install', '-y', package_name)
        return self._docker.run(args=cmd_args, retcode=None)
