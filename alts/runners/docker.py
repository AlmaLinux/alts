import os
from typing import Union, List

from plumbum import local

from alts.runners.base import BaseRunner, TEMPLATE_LOOKUP
from shared.types import ImmutableDict
from shared.exceptions import (ProvisionError, WorkDirPreparationError)


__all__ = ['DockerRunner']


class DockerRunner(BaseRunner):
    SUPPORTED_DISTRIBUTIONS = ('almalinux', 'centos', 'debian', 'ubuntu')
    SUPPORTED_ARCHITECTURES = ('x86_64', 'i686', 'amd64', 'aarch64', 'arm64')
    COST = 0
    ARCHITECTURES_MAPPING = ImmutableDict(
        aarch64=['arm64', 'aarch64'],
        x86_64=['x86_64', 'amd64', 'i686'],
    )
    DOCKER_TF_FILE = 'docker.tf'
    TEMPFILE_PREFIX = 'docker_test_runner_'

    def __init__(self, task_id: str, dist_name: str,
                 dist_version: Union[str, int], repositories: List[dict]):
        super().__init__(task_id, dist_name, dist_version, repositories)
        self._ansible_connection_type = 'docker'

    def prepare_work_dir_files(self):
        try:
            super().prepare_work_dir_files()
            env_name = str(self._env_id)
            docker_template = TEMPLATE_LOOKUP.get_template(
                f'{self.DOCKER_TF_FILE}.tmpl')
            docker_tf_file = os.path.join(self._work_dir, self.DOCKER_TF_FILE)
            with open(docker_tf_file, 'w') as f:
                file_content = docker_template.render(
                    dist_name=self._dist_name, dist_version=self._dist_version,
                    container_name=env_name
                )
                f.write(file_content)
        except WorkDirPreparationError:
            raise
        except Exception as e:
            raise WorkDirPreparationError('Cannot create working directory and'
                                          ' needed files') from e

    def _exec(self, cmd_with_args: ()):
        cmd = ('exec', str(self._env_id), *cmd_with_args)
        cmd_str = ' '.join(cmd)
        self._logger.debug(f'Running "docker {cmd_str}" command')
        return local['docker'].run(args=cmd, retcode=None, cwd=self._work_dir)

    def initial_provision(self, verbose=False):
        # Installing python3 package before running Ansible
        if self._dist_name in self._debian_flavors:
            self._logger.info('Installing python3 package...')
            exit_code, stdout, stderr = self._exec(
                (self._pkg_manager, 'update'))
            if exit_code != 0:
                raise ProvisionError(f'Cannot update metadata: {stderr}')
            cmd_args = (self._pkg_manager, 'install', '-y', 'python3')
            exit_code, stdout, stderr = self._exec(cmd_args)
            if exit_code != 0:
                raise ProvisionError(f'Cannot install package python3: '
                                     f'{stderr}')
            self._logger.info('Installation is completed')
        return super().initial_provision(verbose=verbose)
