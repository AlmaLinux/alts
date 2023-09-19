from typing import Tuple

import pytest

from alts.worker.runners import DockerRunner

# from pyfakefs.fake_filesystem_unittest import TestCase


class TestDockerRunner:
    @pytest.mark.parametrize(
        'inputs, pkg_manager',
        [
            pytest.param(
                ('test_id_1', 'fedora', '33'),
                'dnf',
                id='fedora_33',
            ),
            pytest.param(
                ('test_id_2', 'centos', '7'),
                'yum',
                id='centos_8',
            ),
            pytest.param(
                ('test_id_3', 'centos', '8'),
                'dnf',
                id='centos_8',
            ),
            pytest.param(
                ('test_id_4', 'ubuntu', '20.04'),
                'apt-get',
                id='ubuntu_20.04',
            ),
            pytest.param(
                ('test_id_5', 'debian', '11.0'),
                'apt-get',
                id='debian_11.0',
            ),
            pytest.param(
                ('test_id_6', 'almalinux', '8.3'),
                'dnf',
                id='almalinux_8.3',
            ),
        ],
    )
    def test_docker_runner_init(
        self,
        inputs: Tuple[str, str, str],
        pkg_manager: str,
    ):
        expected = {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': pkg_manager,
        }
        runner = DockerRunner(*inputs)
        assert isinstance(runner.dist_name, str)
        assert isinstance(runner.dist_version, str)
        for attribute in (
            'ansible_connection_type',
            'repositories',
            'pkg_manager',
        ):
            assert getattr(runner, attribute) == expected[attribute]

    # def setUp(self) -> None:
    #     self.patcher = patch('os.stat', MagicMock())
    #     self.another_patcher = patch('os.path.exists', MagicMock())
    #     self.patcher.start()
    #     self.another_patcher.start()
    #
    # def tearDown(self) -> None:
    #     self.patcher.stop()
    #     self.another_patcher.stop()

    # @patch('alts.runners.base.tempfile.mkdtemp')
    # def test_working_directory_creation(self, p_work_dir):
    #     p_work_dir.return_value = self._work_dir
    #     runner = DockerRunner(*centos_runner_params)
    #
    #     with patch('os.path.exists') as exists, patch('os.stat') as stat:
    #         exists.return_value = False
    #         stat.return_value = MagicMock()
    #         runner._create_work_dir()
    #         self.assertEqual(runner._work_dir, self._work_dir)
