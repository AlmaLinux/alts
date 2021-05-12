import logging

from alts.worker.app import celery_app
from alts.worker.mappings import RUNNER_MAPPING


__all__ = ['run_tests']


@celery_app.task()
def run_tests(task_params: dict):

    logging.info(f'Starting work with the following params: {task_params}')

    for key in ['task_id', 'runner_type', 'dist_name', 'dist_version',
                'dist_arch', 'repositories', 'package_name']:
        if task_params.get(key) is None:
            logging.error(f'Parameter {key} is not specified')
            return

    runner_args = (task_params['task_id'], task_params['dist_name'],
                   task_params['dist_version'])

    runner_kwargs = {'repositories': task_params.get('repositories')
                     if task_params.get('repositories') else [],
                     'dist_arch': task_params.get('dist_arch')
                     if task_params.get('dist_arch') else 'x86_64'}

    runner_class = RUNNER_MAPPING[task_params['runner_type']]
    runner = runner_class(*runner_args, **runner_kwargs)
    try:
        runner.setup()
        runner.install_package(task_params['package_name'],
                               task_params.get('package_version'))
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
