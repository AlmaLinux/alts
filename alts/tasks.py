import logging
from typing import List, Union

from alts.app import celery_app
from shared import RUNNER_MAPPING


__all__ = ['run_docker']


def execute_tests(task_id: str, runner_type: str, dist_name: str,
                  dist_version: Union[str, int], repositories: List[dict],
                  package_name: str, package_version: str = None):

    if not all([item is not None for item in
                [task_id, runner_type, dist_name, dist_version, repositories,
                 package_name]]):
        logging.error('Please specify parameters')
        return
    runner_class = RUNNER_MAPPING[runner_type]
    runner = runner_class(task_id, dist_name, dist_version, repositories)
    try:
        runner.setup()
        runner.install_package(package_name, package_version)
    finally:
        runner.teardown()

    # TODO: Add summary for tests execution
    summary = {}
    for stage, stage_data in runner.artifacts.items():
        if stage_data['exit_code'] == 0:
            success = True
        else:
            success = False
        summary[stage] = {'success': success}

    return summary


@celery_app.task()
def run_docker(task_id: str, runner_type: str, dist_name: str,
               dist_version: Union[str, int], repositories: List[dict],
               package_name: str, package_version: str = None):
    return execute_tests(task_id, runner_type, dist_name, dist_version,
                         repositories, package_name, package_version)
