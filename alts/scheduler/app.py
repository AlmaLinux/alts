# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System tasks scheduler application."""

import logging
import signal
from threading import Event

from celery.exceptions import TimeoutError
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from jose import JWTError, jwt

from alts.scheduler import CONFIG
from alts.scheduler.db import Session, Task, database
from alts.scheduler.monitoring import TasksMonitor
from alts.shared.constants import API_VERSION
from alts.shared.exceptions import ALTSBaseError
from alts.shared.models import TaskResultResponse
from alts.worker.app import celery_app

app = FastAPI()
monitor = None
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
    task_data = celery_app.AsyncResult(task_id)
    try:
        result['result'] = task_data.get(timeout=timeout)
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
    result['state'] = task_data.state
    return result


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
                task_result = celery_app.AsyncResult(task.task_id)
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
    monitor.start()


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
    task_result = get_celery_task_result(task_id)
    task_result['api_version'] = API_VERSION
    return JSONResponse(content=task_result)
