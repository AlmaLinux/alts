# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-04-13

"""AlmaLinux Test System package testing tasks running."""

import logging
import urllib.parse

import requests
import tap.parser

from alts.shared.constants import API_VERSION
from alts.worker import CONFIG
from alts.worker.app import celery_app
from alts.worker.mappings import RUNNER_MAPPING
from alts.worker.runners.base import TESTS_SECTION_NAME


__all__ = ['run_tests']


def are_tap_tests_success(tests_output: str):
    """
    Checks if TAP tests were successful. Returns one of the 3 values:
    True - all tests are successful
    False - one or more tests have failed
    None - not a TAP input

    Parameters
    ----------
    tests_output

    Returns
    -------

    """
    parser = tap.parser.Parser()
    try:
        tap_data = list(parser.parse(tests_output))
    except Exception:
        return None
    errors = 0
    for test_case in tap_data:
        if test_case.category == 'test':
            if test_case.todo:
                continue
            elif test_case.skip:
                continue
            elif test_case.ok:
                continue
            else:
                errors += 1
    return errors == 0


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

    def is_success(stage_data_: dict):
        tap_result = are_tap_tests_success(stage_data_['stdout'])
        if tap_result is not None:
            return tap_result
        return stage_data_['exit_code'] == 0

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
                    log_file_name = f'{stage}_{inner_stage}.log'
                    error_log = f'{stage}_{inner_stage}_error.log'
                    stage_dict = {
                        'success': is_success(inner_data),
                        'output': inner_data['stdout']
                    }
                    if log_file_name in runner.uploaded_logs:
                        stage_dict['log'] = runner.uploaded_logs[log_file_name]
                    if error_log in runner.uploaded_logs:
                        stage_dict['error_log'] = \
                            runner.uploaded_logs[error_log]
                    summary[TESTS_SECTION_NAME][inner_stage] = stage_dict
            else:
                log_file_name = f'{stage}.log'
                error_log = f'{stage}_error.log'
                stage_dict = {'success': is_success(stage_data)}
                if log_file_name in runner.uploaded_logs:
                    stage_dict['log'] = runner.uploaded_logs[log_file_name]
                if error_log in runner.uploaded_logs:
                    stage_dict['error_log'] = runner.uploaded_logs[error_log]
                summary[stage] = stage_dict
        if task_params.get('callback_href'):
            full_url = urllib.parse.urljoin(CONFIG.bs_host,
                                            task_params['callback_href'])
            payload = {'api_version': API_VERSION, 'result': summary}
            response = requests.post(
                full_url, json=payload,
                headers={'Authorization': f'Bearer {CONFIG.bs_token}'})
            response.raise_for_status()

        return summary
