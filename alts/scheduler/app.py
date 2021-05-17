# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System tasks scheduler application."""

import logging
import random
import time
import traceback
import uuid
from datetime import datetime, timedelta

import requests
from celery.exceptions import TimeoutError
from celery.states import READY_STATES
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from pydantic import ValidationError

from alts.worker.app import celery_app
from alts.worker.mappings import RUNNER_MAPPING
from alts.worker.tasks import run_tests
from alts.scheduler import CONFIG
from alts.scheduler.db import database, Session, Task
from alts.shared.models import (
    TaskRequestResponse,
    TaskRequestPayload,
    TaskResultResponse,
)

# YYYYMMDD format for API version
API_VERSION = '20210512'
app = FastAPI()
http_bearer_scheme = HTTPBearer()


def get_celery_task_result(task_id: str, timeout: int = 1) -> dict:
    """
    Gets Test System task result info from Celery.

    Parameters
    ----------
    task_id : str
        Test System task identifier.
    timeout : int
        How long to wait before the operation to get result times out
        (in seconds).
    Returns
    -------
    dict
        Test System task result.

    """
    result = {}
    task_data = celery_app.AsyncResult(task_id)
    try:
        result['result'] = task_data.get(timeout=timeout)
    except TimeoutError:
        pass
    result['state'] = task_data.state
    return result


# TODO: Make background functions react to application stop
def check_celery_task_result(task_id: str, callback_url: str = None):
    """
    Gets Test System task result info from Celery.

    Parameters
    ----------
    task_id : str
        Test System task identifier.
    callback_url : str
        Url to update Test System task execution result.
    """
    task_status = None
    later = datetime.now() + timedelta(seconds=CONFIG.task_tracking_timeout)
    session = Session()
    logging.info(f'Starting to monitor task {task_id}')
    while task_status not in READY_STATES and datetime.now() <= later:
        try:
            task_result = celery_app.AsyncResult(task_id)
            task_status = task_result.state
        except Exception as e:
            logging.error(f'Cannot fetch task result for task ID {task_id}:'
                          f' {e}')
        try:
            task_record = (session.query(Task).filter(Task.task_id == task_id)
                           .first())
            if task_record.status != task_status:
                task_record.status = task_status
                session.add(task_record)
                session.commit()
                logging.info(f'Updated task {task_id} status to {task_status}')
        except Exception as e:
            logging.error(f'Cannot update task DB record: {e}')
        time.sleep(10)

    if callback_url:
        task_result = get_celery_task_result(task_id)
        task_result['api_version'] = API_VERSION
        requests.post(callback_url, json=task_result)

    logging.info(f'Finished monitoring for task {task_id}')


@app.on_event('startup')
async def startup():

    """Starting up Test System task scheduler app."""

    logging.basicConfig(level=logging.INFO)
    await database.connect()

    session = Session()
    # TODO: Get workers capacity and queues mapping
    # TODO: Get queues maximum capacity
    # inspect_instance = celery_app.control.inspect()
    # for _, tasks in inspect_instance.active(safe=True).items():
    #     # TODO: Add query to database and update tasks
    #     pass
    try:
        for task in (session.query(Task.task_id, Task.status)
                     .filter(Task.status == 'STARTED')):
            task_result = celery_app.AsyncResult(task.task_id)
            task.status = task_result.state
            session.add(task)
        session.commit()
    except Exception as e:
        logging.error(f'Cannot save task info: {e}')
        session.rollback()


@app.on_event('shutdown')
async def shutdown():

    """Shutting down Test System task scheduler app."""

    await database.disconnect()


async def authenticate_user(credentials: str = Depends(http_bearer_scheme)):
    """
    Authenticates user via jwt token.

    Parameters
    ----------
    credentials : str
        Http authentication scheme info with token.

    Returns
    -------
    dict
        Decoded information from jwt token.
    """
    # TODO: Validate user emails?
    try:
        # If credentials have a whitespace then the token is the part after
        # the whitespace
        if ' ' in credentials.credentials:
            token = credentials.credentials.split(' ')[-1]
        else:
            token = credentials.credentials
        return jwt.decode(token, CONFIG.jwt_secret,
                          algorithms=[CONFIG.hashing_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"})


@app.get('/tasks/{task_id}/result', response_model=TaskResultResponse)
async def get_task_result(task_id: str,
                          _=Depends(authenticate_user)) -> JSONResponse:
    """
    Requests for Test System task result.

    Parameters
    ----------
    task_id : str
        Test System task identifier.
    _ : dict
        Authenticated user's token.

    Returns
    -------
    JSON
        JSON-encoded response with task result.
    """
    task_result = get_celery_task_result(task_id)
    task_result['api_version'] = API_VERSION
    return JSONResponse(content=task_result)


@app.post('/tasks/schedule', response_model=TaskRequestResponse,
          responses={
              201: {'model': TaskRequestResponse},
              400: {'model': TaskRequestResponse},
          })
async def schedule_task(task_data: TaskRequestPayload,
                        b_tasks: BackgroundTasks,
                        _=Depends(authenticate_user)) -> JSONResponse:
    """
    Schedules new tasks in Test System.

    Parameters
    ----------
    task_data : TaskRequestPayload
        Loader task data in appropriate for request form.
    b_tasks : BackgroundTasks
        Tasks running in background.
    _ : dict
        Authenticated user's token.

    Returns
    -------
    JSONResponse
        JSON-encoded response if task executed successfully or not.
    """
    # Get only supported runners mapping based on the config
    if isinstance(CONFIG.supported_runners, str) and \
            CONFIG.supported_runners == 'all':
        runner_mapping = RUNNER_MAPPING
    elif isinstance(CONFIG.supported_runners, list):
        runner_mapping = {key: value for key, value in RUNNER_MAPPING.items()
                          if key in CONFIG.supporter_runners}
    else:
        raise ValidationError(f'Misconfiguration found: supported_runners is '
                              f'{CONFIG.supported_runners}')
    runner_type = task_data.runner_type
    if runner_type == 'any':
        runner_type = random.choice(list(runner_mapping.keys()))
    runner_class = RUNNER_MAPPING[runner_type]

    if task_data.dist_arch not in CONFIG.supported_architectures:
        raise ValidationError(f'Unknown architecture: {task_data.dist_arch}')
    if task_data.dist_name not in CONFIG.supported_distributions:
        raise ValidationError(f'Unknown distribution: {task_data.dist_name}')

    # TODO: Make decision on what queue to use for particular task based on
    #  queues load
    queue_arch = None
    for arch, supported_arches in runner_class.ARCHITECTURES_MAPPING.items():
        if task_data.dist_arch in supported_arches:
            queue_arch = arch

    if not queue_arch:
        raise ValidationError('Cannot map requested architecture to any '
                              'host architecture, possible coding error')

    # Make sure all repositories have their names
    # (needed only for RHEL-like distributions)
    # Convert repositories structures to dictionaries
    repositories = []
    repo_counter = 0
    for repository in task_data.repositories:
        if not repository.name:
            repo_name = f'repo-{repo_counter}'
            repo_counter += 1
        else:
            repo_name = repository.name
        repositories.append({'url': repository.baseurl, 'name': repo_name})

    queue_name = f'{runner_type}-{queue_arch}-{runner_class.COST}'
    task_id = str(uuid.uuid4())
    response_content = {'api_version': API_VERSION}
    task_params = task_data.dict()
    task_params['task_id'] = task_id
    task_params['runner_type'] = runner_type
    try:
        run_tests.apply_async((task_params,), task_id=task_id,
                              queue=queue_name)
    except Exception as e:
        logging.error(f'Cannot launch the task: {e}')
        logging.error(traceback.format_exc())
        response_content.update(
            {'success': False, 'error_description': str(e)})
        return JSONResponse(status_code=400, content=response_content)
    else:
        session = Session()
        try:
            task_record = Task(task_id=task_id, queue_name=queue_name,
                               status='NEW')
            session.add(task_record)
            session.commit()
            b_tasks.add_task(check_celery_task_result, task_id,
                             callback_url=task_params.get('callback_url', ''))
            response_content.update({'success': True, 'task_id': task_id})
            return JSONResponse(status_code=201, content=response_content)
        except Exception as e:
            logging.error(f'Cannot save task data into DB: {e}')
            session.rollback()
            response_content.update({'success': False, 'task_id': task_id,
                                     'error_description': str(e)})
            return JSONResponse(status_code=400, content=response_content)
