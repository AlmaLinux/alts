# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-04-13

"""AlmaLinux Test System package testing tasks running."""

import logging
import urllib.parse
from collections import defaultdict
from typing import Union

import requests
import tap.parser

from alts.shared.constants import API_VERSION, DEFAULT_REQUEST_TIMEOUT
from alts.shared.exceptions import (
    InstallPackageError,
    PackageIntegrityTestsError,
    ProvisionError,
    StartEnvironmentError,
    StopEnvironmentError,
    TerraformInitializationError,
    ThirdPartyTestError,
    UninstallPackageError,
)
from alts.worker import CONFIG
from alts.worker.app import celery_app
from alts.worker.mappings import RUNNER_MAPPING
from alts.worker.runners.base import TESTS_SECTIONS_NAMES
from alts.worker.runners.docker import DockerRunner
from alts.worker.runners.opennebula import OpennebulaRunner

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
            if any((test_case.todo, test_case.skip, test_case.ok)):
                continue
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
        tap_result = are_tap_tests_success(stage_data_.get('stdout', ''))
        if tap_result is not None:
            return tap_result
        return stage_data_['exit_code'] == 0

    def set_artifacts_when_stage_has_unexpected_exception(
        _artifacts: dict,
        error_message: str,
        section_name: str,
    ):
        if section_name not in _artifacts:
            _artifacts[section_name] = {}
        _artifacts_section = _artifacts[section_name]
        _artifacts_section = {
            'exit_code': 1,
            'stdout': error_message,
        }

    logging.info('Starting work with the following params: %s', task_params)

    for key in [
        'task_id',
        'runner_type',
        'dist_name',
        'dist_version',
        'dist_arch',
        'repositories',
        'package_name',
    ]:
        if task_params.get(key) is None:
            logging.error('Parameter %s is not specified', key)
            return

    runner_args = (
        task_params['task_id'],
        task_params['dist_name'],
        task_params['dist_version'],
    )

    runner_kwargs = {
        'repositories': task_params.get('repositories', []),
        'dist_arch': task_params.get('dist_arch', 'x86_64'),
        'test_configuration': task_params.get('test_configuration', {}),
    }

    runner_class = RUNNER_MAPPING[task_params['runner_type']]
    runner: Union[DockerRunner, OpennebulaRunner] = runner_class(
        *runner_args,
        **runner_kwargs,
    )
    module_name = task_params.get('module_name')
    module_stream = task_params.get('module_stream')
    module_version = task_params.get('module_version')
    try:
        package_name = task_params['package_name']
        package_version = task_params.get('package_version')
        runner.setup()
        runner.run_system_info_commands()
        runner.install_package(
            package_name,
            package_version,
            module_name=module_name,
            module_stream=module_stream,
            module_version=module_version,
        )
        if CONFIG.enable_integrity_tests:
            runner.run_package_integrity_tests(package_name, package_version)
        runner.run_third_party_tests()
        runner.uninstall_package(
            package_name,
            package_version,
            module_name=module_name,
            module_stream=module_stream,
            module_version=module_version,
        )
    except TerraformInitializationError as exc:
        logging.exception('Cannot initial terraform: %s', exc)
    except StartEnvironmentError as exc:
        logging.exception('Cannot start environment: %s', exc)
    except ProvisionError as exc:
        logging.exception('Cannot run initial provision: %s', exc)
    except InstallPackageError as exc:
        logging.exception('Cannot install package: %s', exc)
    except PackageIntegrityTestsError as exc:
        logging.exception('Package integrity tests failed: %s', exc)
    except ThirdPartyTestError as exc:
        logging.exception('Third party tests failed: %s', exc)
    except UninstallPackageError as exc:
        logging.exception('Cannot uninstall package: %s', exc)
    except StopEnvironmentError as exc:
        logging.exception('Cannot stop environment: %s', exc)
    except Exception as exc:
        logging.exception('Unexpected exception: %s', exc)
        set_artifacts_when_stage_has_unexpected_exception(
            _artifacts=runner.artifacts,
            error_message=f'Unexpected exception: {exc}',
            section_name='Unexpected errors during tests',
        )
    finally:
        runner.teardown()
        summary = defaultdict(dict)
        for stage, stage_data in runner.artifacts.items():
            # FIXME: Temporary solution, needs to be removed when this
            #  test system will be the only running one
            if stage not in TESTS_SECTIONS_NAMES:
                stage_info = {'success': is_success(stage_data)}
                if CONFIG.logs_uploader_config.skip_artifacts_upload:
                    stage_info.update(stage_data)
                summary[stage] = stage_info
                continue
            if stage not in summary:
                summary[stage] = {}
            for inner_stage, inner_data in stage_data.items():
                stage_info = {
                    'success': is_success(inner_data),
                    'output': inner_data['stdout'],
                }
                if CONFIG.logs_uploader_config.skip_artifacts_upload:
                    stage_info.update(inner_data)
                summary[stage][inner_stage] = stage_info
        if runner.uploaded_logs:
            summary['logs'] = runner.uploaded_logs
        if task_params.get('callback_href'):
            full_url = urllib.parse.urljoin(
                CONFIG.bs_host,
                task_params['callback_href'],
            )
            payload = {
                'api_version': API_VERSION,
                'result': summary,
                'stats': runner.stats,
            }
            response = requests.post(
                full_url,
                json=payload,
                headers={'Authorization': f'Bearer {CONFIG.bs_token}'},
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

        return summary
