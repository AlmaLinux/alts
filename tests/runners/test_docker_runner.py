from unittest import TestCase

from ddt import ddt
from ddt import data, unpack
# from pyfakefs.fake_filesystem_unittest import TestCase

from alts.worker.runners import DockerRunner

fedora_runner_params = ('test_id_1', 'fedora', '33')
centos_8_runner_params = ('test_id_2', 'centos', 8)
centos_7_runner_params = ('test_id_3', 'centos', 7)
ubuntu_runner_params = ('test_id_4', 'ubuntu', '20.04')
debian_runner_params = ('test_id_5', 'debian', '11.0')
almalinux_runner_params = ('test_id_6', 'almalinux', '8.3')

basics_data = (
    (
        centos_8_runner_params,
        {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': 'dnf'
        }
    ),
    (
        centos_7_runner_params,
        {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': 'yum'
        }
    ),
    (
        ubuntu_runner_params,
        {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': 'apt-get'
        }
    ),
    (
        fedora_runner_params,
        {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': 'dnf'
        }
    ),
    (
        debian_runner_params,
        {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': 'apt-get'
        }
    ),
    (
        almalinux_runner_params,
        {
            'ansible_connection_type': 'docker',
            'repositories': [],
            'pkg_manager': 'dnf'
        }
    ),
)


@ddt
class TestDockerRunner(TestCase):

    @data(*basics_data)
    @unpack
    def test_basics(self, inputs: tuple, expected: dict):
        runner = DockerRunner(*inputs)
        self.assertIsInstance(runner.dist_name, str)
        self.assertIsInstance(runner.dist_version, str)
        for attribute in ('ansible_connection_type', 'repositories',
                          'pkg_manager'):
            self.assertEqual(getattr(runner, attribute), expected[attribute])

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
