# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-04-13

"""AlmaLinux Test System package testing tasks running."""

import logging

from alts.worker.app import celery_app
from alts.worker.mappings import RUNNER_MAPPING
from alts.worker.runners.base import TESTS_SECTION_NAME


__all__ = ['run_tests']


@celery_app.task()
def run_tests(task_params: dict):
    """
    Executes a package test in a specified environment.

    Parameters
    ----------
    task_params : dict
        Task parameters.

    Returns
    -------
    dict
        Result summary of a test execution.
    """

    def is_success(stage_data: dict):
        return stage_data['exit_code'] == 0

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
        package_name = task_params['package_name']
        package_version = task_params.get('package_version')
        runner.setup()
        runner.install_package(package_name, package_version)
        runner.run_package_integrity_tests(package_name, package_version)
    finally:
        runner.teardown()
        summary = {}
        for stage, stage_data in runner.artifacts.items():
            # FIXME: Temporary solution, needs to be removed when this
            #  test system will be the only running one
            if stage == TESTS_SECTION_NAME:
                if TESTS_SECTION_NAME not in summary:
                    summary[TESTS_SECTION_NAME] = {}
                for inner_stage, inner_data in stage_data.items():
                    summary[TESTS_SECTION_NAME][inner_stage] = {
                        'success': is_success(inner_data),
                        'output': inner_data['stdout']
                    }
            else:
                summary[stage] = {'success': is_success(stage_data)}

        return summary
