# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System tasks scheduler application."""

import logging
import requests
import signal
import urllib.parse
from threading import Event

from celery.contrib.abortable import AbortableAsyncResult
from celery.exceptions import TimeoutError
from celery.states import READY_STATES, RECEIVED, STARTED
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select

from alts.scheduler import CONFIG
from alts.scheduler.db import Session, Task, database
from alts.scheduler.monitoring import TasksMonitor
from alts.scheduler.scheduling import TestsScheduler
from alts.shared.constants import API_VERSION, DEFAULT_REQUEST_TIMEOUT
from alts.shared.exceptions import ALTSBaseError
from alts.shared.models import CancelTaskResponse, TaskResultResponse
from alts.worker.app import celery_app

app = FastAPI()
monitor = None
scheduler = None
terminate_event = Event()
graceful_terminate_event = Event()
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
    task = AbortableAsyncResult(task_id, app=celery_app)
    try:
        result['result'] = task.get(timeout=timeout)
    except TimeoutError:
        pass
    except ALTSBaseError as e:
        logging.warning(
            'Task has failed with error: %s: %s',
            e.__class__.__name__,
            e,
        )
    except Exception:
        logging.exception(
            'Unknown exception while getting results from Celery',
        )
    result['state'] = task.state
    return task, result


@app.on_event('startup')
async def startup():
    """Starting up Test System task scheduler app."""

    logging.basicConfig(level=logging.INFO)
    await database.connect()

    # TODO: Get workers capacity and queues mapping
    # TODO: Get queues maximum capacity
    # inspect_instance = celery_app.control.inspect()
    # for _, tasks in inspect_instance.active(safe=True).items():
    #     # TODO: Add query to database and update tasks
    #     pass
    with Session() as session:
        with session.begin():
            tasks_for_update = []
            for task in session.query(Task).filter(Task.status == 'STARTED'):
                task_result = AbortableAsyncResult(task.task_id, app=celery_app)
                if task.status != task_result.state:
                    task.status = task_result.state
                    tasks_for_update.append(task)
            if tasks_for_update:
                try:
                    session.add_all(tasks_for_update)
                    session.commit()
                except Exception:
                    logging.exception('Cannot save tasks info:')
    del tasks_for_update

    global graceful_terminate_event
    global terminate_event
    global monitor
    global scheduler

    def signal_handler(signum, frame):
        logging.info('Terminating all threads...')
        terminate_event.set()

    def sigusr_handler(signum, frame):
        logging.info('Gracefully terminating all threads...')
        graceful_terminate_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGUSR1, sigusr_handler)

    monitor = TasksMonitor(
        terminate_event,
        graceful_terminate_event,
        celery_app,
    )
    scheduler = TestsScheduler(
        terminate_event,
        graceful_terminate_event,
        celery_app,
    )
    monitor.start()
    scheduler.start()


@app.on_event('shutdown')
async def shutdown():
    """Shutting down Test System task scheduler app."""

    graceful_terminate_event.set()
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
        return jwt.decode(
            token,
            CONFIG.jwt_secret,
            algorithms=[CONFIG.hashing_algorithm],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get('/tasks/{task_id}/result', response_model=TaskResultResponse)
async def get_task_result(
    task_id: str,
    _=Depends(authenticate_user),
) -> JSONResponse:
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
    _, task_result = get_celery_task_result(task_id)
    task_result['api_version'] = API_VERSION
    return JSONResponse(content=task_result)


@app.post('/cancel_tasks', response_model=CancelTaskResponse)
async def cancel_task(
    payload: dict,
    _=Depends(authenticate_user),
) -> JSONResponse:
    """
    Requests for cancelling tasks.

    Parameters
    ----------
    task_id : list(str)
        Test System task identifier.
    _ : dict
        Authenticated user's token.

    Returns
    -------
    JSON
        JSON-encoded response with task result.
    """
    with Session() as session, session.begin():
        db_tasks = (
            session.execute(
                select(Task).where(
                    Task.status.notin_(READY_STATES),
                    Task.albs_task_id.in_(payload['albs_task_ids'])
                )
            )
        ).scalars().all()
        task_ids = {
            db_task.albs_task_id:db_task.task_id
            for db_task in db_tasks
        }

        logging.info(f'cancel_tests have been called - {payload=}')
        logging.info(f'Current tasks in db - {task_ids=}')
        celery_app.control.revoke(
            list(task_ids.values()),
        #    # Terminating only works on eventlet and prefork Celery pools
        #    terminate=True,
        )
        for db_task in db_tasks:
            task, _ = get_celery_task_result(db_task.task_id)
            # Here we only post revoked test results that are still
            # waiting to be enqueued/processed by workers.
            # The rest of the tests results are posted by the workers.
            if task.state in (RECEIVED, STARTED):
                task.abort()
                continue

            full_url = urllib.parse.urljoin(
                CONFIG.bs_host,
                f'/api/v1/tests/{db_task.albs_task_id}/result/',
            )
            payload = {
                'api_version': API_VERSION,
                'result': {
                    'revoked': True
                },
                'stats': {}
            }
            response = requests.post(
                full_url,
                json=payload,
                headers={'Authorization': f'Bearer {CONFIG.bs_token}'},
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
    result = { 'success': True }
    return JSONResponse(content=result)
