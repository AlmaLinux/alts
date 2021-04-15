from typing import List, Union

from alts_app import app
from alts.runners import DockerRunner


@app.task()
def test_task(task_id: str, dist_name: str, dist_version: Union[str, int],
              repositories: List[dict], package_name: str):
    if not all([item is not None for item in [task_id, dist_name, dist_version, repositories, package_name]]):
        print('Please specify parameters')
        return
    runner = DockerRunner(task_id, dist_name, dist_version, repositories)
    try:
        runner.setup()
        runner.install_package(package_name)
        runner.publish_artifacts_to_storage()
    finally:
        runner.teardown()
