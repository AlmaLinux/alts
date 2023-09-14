# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-04-13

"""AlmaLinux Test System package testing tasks running."""

import logging
import urllib.parse

import requests
import tap.parser

from alts.shared.constants import API_VERSION, DEFAULT_REQUEST_TIMEOUT
from alts.shared.exceptions import (
    InstallPackageError,
    UninstallPackageError,
    PackageIntegrityTestsError,
    StopEnvironmentError,
)
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

    logging.info('Starting work with the following params: %s', task_params)

    for key in ['task_id', 'runner_type', 'dist_name', 'dist_version',
                'dist_arch', 'repositories', 'package_name']:
        if task_params.get(key) is None:
            logging.error('Parameter %s is not specified', key)
            return

    runner_args = (task_params['task_id'], task_params['dist_name'],
                   task_params['dist_version'])

    runner_kwargs = {
        'repositories': task_params.get('repositories')
                        if task_params.get('repositories') else [],
        'dist_arch': task_params.get('dist_arch')
                     if task_params.get('dist_arch') else 'x86_64',
        'test_configuration': task_params.get('test_configuration')
                              if task_params.get('test_configuration') else {},
    }

    runner_class = RUNNER_MAPPING[task_params['runner_type']]
    runner = runner_class(*runner_args, **runner_kwargs)
    module_name = task_params.get('module_name')
    module_stream = task_params.get('module_stream')
    module_version = task_params.get('module_version')
    try:
        package_name = task_params['package_name']
        package_version = task_params.get('package_version')
        runner.setup()
        runner.install_package(
            package_name, package_version,
            module_name=module_name, module_stream=module_stream,
            module_version=module_version
        )
        runner.run_package_integrity_tests(package_name, package_version)
        runner.uninstall_package(
            package_name, package_version,
            module_name=module_name, module_stream=module_stream,
            module_version=module_version
        )
    except StopEnvironmentError as exc:
        logging.exception('Cannot start environment: %s', exc)
    except InstallPackageError as exc:
        logging.exception('Cannot install package: %s', exc)
    except PackageIntegrityTestsError as exc:
        logging.exception('Package integrity tests failed: %s', exc)
    except UninstallPackageError as exc:
        logging.exception('Cannot uninstall package: %s', exc)
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
        summary['logs'] = runner.uploaded_logs
        if task_params.get('callback_href'):
            full_url = urllib.parse.urljoin(CONFIG.bs_host,
                                            task_params['callback_href'])
            payload = {'api_version': API_VERSION, 'result': summary,
                       'stats': runner.stats}
            response = requests.post(
                full_url, json=payload,
                headers={'Authorization': f'Bearer {CONFIG.bs_token}'},
                timeout=DEFAULT_REQUEST_TIMEOUT
            )
            response.raise_for_status()

        return summary
