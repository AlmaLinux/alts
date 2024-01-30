import logging
import random
import threading
import time
import urllib.parse
import uuid
from typing import List

import requests

from alts.scheduler import CONFIG
from alts.scheduler.db import Session, Task
from alts.shared.models import TaskRequestPayload
from alts.worker.mappings import RUNNER_MAPPING
from alts.worker.tasks import run_tests


class TestsScheduler(threading.Thread):
    def __init__(
        self,
        terminated_event: threading.Event,
        graceful_terminate: threading.Event,
        celery_app,
        get_result_timeout: int = 1,
    ):
        super().__init__()
        self.__terminated_event = terminated_event
        self.__graceful_terminate = graceful_terminate
        self.__celery = celery_app
        self.__get_result_timeout = get_result_timeout
        self.logger = logging.getLogger(__file__)

    def get_available_test_tasks(self) -> List[dict]:
        response_as_json = []
        try:
            self.logger.info('Getting new available test tasks')
            response = requests.get(
                urllib.parse.urljoin(
                    CONFIG.bs_host,
                    CONFIG.bs_tasks_endpoint,
                ),
                headers={'Authorization': f'Bearer {CONFIG.bs_token}'},
            )
            response.raise_for_status()
            response_as_json = response.json()
            if not response_as_json:
                self.logger.info('There are no available test tasks')
        except Exception:
            self.logger.exception('Cannot get available test tasks:')
        return response_as_json

    def schedule_test_task(self, payload: TaskRequestPayload):
        """
        Schedules new tasks in Test System.

        Parameters
        ----------
        payload : TaskRequestPayload
            Loader task data in appropriate for request form.

        Returns
        -------
        JSONResponse
            JSON-encoded response if task executed successfully or not.
        """
        # Get only supported runners mapping based on the config
        if (
            isinstance(CONFIG.supported_runners, str)
            and CONFIG.supported_runners == 'all'
        ):
            runner_mapping = RUNNER_MAPPING
        elif isinstance(CONFIG.supported_runners, list):
            runner_mapping = {
                key: value
                for key, value in RUNNER_MAPPING.items()
                if key in CONFIG.supported_runners
            }
        else:
            self.logger.error(
                'Misconfiguration found: supported_runners is %s',
                CONFIG.supported_runners,
            )
            return
        runner_type = payload.runner_type
        if runner_type == 'any':
            runner_type = random.choice(list(runner_mapping.keys()))
        runner_class = RUNNER_MAPPING[runner_type]

        if payload.dist_arch not in CONFIG.supported_architectures:
            self.logger.error('Unknown architecture: %s', payload.dist_arch)
            return
        if payload.dist_name not in CONFIG.supported_distributions:
            self.logger.error('Unknown distribution: %s', payload.dist_name)
            return

        # TODO: Make decision on what queue to use for particular task based on
        #  queues load
        queue_arch = None
        for (
            arch,
            supported_arches,
        ) in runner_class.ARCHITECTURES_MAPPING.items():
            if payload.dist_arch in supported_arches:
                queue_arch = arch

        if not queue_arch:
            self.logger.error(
                'Cannot map requested architecture to any '
                'host architecture, possible coding error'
            )
            return

        # Make sure all repositories have their names
        # (needed only for RHEL-like distributions)
        # Convert repositories structures to dictionaries
        repositories = []
        repo_counter = 0
        for repository in payload.repositories:
            repo_name = repository.name
            if not repo_name:
                repo_name = f'repo-{repo_counter}'
                repo_counter += 1
            repositories.append({'url': repository.baseurl, 'name': repo_name})

        queue_name = f'{runner_type}-{queue_arch}-{runner_class.COST}'
        task_id = str(uuid.uuid4())
        task_params = payload.model_dump()
        task_params['task_id'] = task_id
        task_params['runner_type'] = runner_type
        task_params['repositories'] = repositories
        try:
            run_tests.apply_async(
                (task_params,),
                task_id=task_id,
                queue=queue_name,
            )
        except Exception:
            # TODO: report error to the web server
            self.logger.exception('Cannot launch the task:')
        with Session() as session, session.begin():
            try:
                task_record = Task(
                    task_id=task_id,
                    bs_task_id=payload.bs_task_id,
                    callback_href=payload.callback_href,
                    queue_name=queue_name,
                    status='NEW',
                )
                session.add(task_record)
                session.commit()
            except Exception:
                self.logger.exception('Cannot save task data into DB:')

    def run(self):
        while (
                not self.__graceful_terminate.is_set()
                or not self.__terminated_event.is_set()
        ):
            for test_task_payload in self.get_available_test_tasks():
                self.schedule_test_task(
                    TaskRequestPayload(**test_task_payload)
                )
            time.sleep(10)
