import logging
import time
import traceback
import uuid
from datetime import datetime, timedelta

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

from alts.app import celery_app
from alts.mappings import RUNNER_MAPPING
from alts.tasks import run_docker
from scheduler import CONFIG
from scheduler.db import database, Session, Task
from shared.models import (
    TaskRequestResponse,
    TaskRequestPayload,
    TaskResultResponse,
)


app = FastAPI()
http_bearer_scheme = HTTPBearer()


def get_celery_task_result(task_id: str, timeout: int = 1) -> dict:
    result = {}
    task_data = celery_app.AsyncResult(task_id)
    try:
        result['result'] = task_data.get(timeout=timeout)
    except TimeoutError:
        pass
    result['state'] = task_data.state
    return result


# TODO: Make timeout configurable
# TODO: Make background functions react to application stop
def check_celery_task_result(task_id: str, timeout=3600):
    task_status = None
    later = datetime.now() + timedelta(seconds=timeout)
    session = Session()
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


@app.on_event('startup')
async def startup():
    logging.basicConfig(level=logging.INFO)
    await database.connect()

    session = Session()
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


@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()


async def authenticate_user(credentials: str = Depends(http_bearer_scheme)):
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
    return JSONResponse(content=get_celery_task_result(task_id))


@app.post('/tasks/schedule', response_model=TaskRequestResponse,
          responses={
              201: {'model': TaskRequestResponse},
              400: {'model': TaskRequestResponse},
          })
async def schedule_task(task_data: TaskRequestPayload,
                        b_tasks: BackgroundTasks,
                        _=Depends(authenticate_user)) -> JSONResponse:
    runner_type = task_data.runner_type
    if runner_type == 'any':
        runner_type = 'docker'
    runner_class = RUNNER_MAPPING[runner_type]

    if task_data.dist_arch not in runner_class.SUPPORTED_ARCHITECTURES:
        raise ValidationError(f'Unknown architecture: {task_data.dist_arch}')
    if task_data.dist_name not in runner_class.SUPPORTED_DISTRIBUTIONS:
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

    queue_name = f'{runner_type}-{queue_arch}-{runner_class.COST}'
    task_id = str(uuid.uuid4())
    try:
        run_docker.apply_async(
            (task_id, runner_type, task_data.dist_name, task_data.dist_version,
             task_data.repositories, task_data.package_name,
             task_data.package_version), task_id=task_id, queue=queue_name)
    except Exception as e:
        logging.error(f'Cannot launch the task: {e}')
        logging.error(traceback.format_exc())
        return JSONResponse(
            content={'success': False, 'error_description': str(e)},
            status_code=400
        )
    else:
        session = Session()
        try:
            task_record = Task(task_id=task_id, queue_name=queue_name,
                               status='NEW')
            session.add(task_record)
            session.commit()
            b_tasks.add_task(check_celery_task_result, task_id)
            return JSONResponse(content={'success': True, 'task_id': task_id},
                                status_code=201)
        except Exception as e:
            logging.error(f'Cannot save task data into DB: {e}')
            session.rollback()
            return JSONResponse(content={'success': False, 'task_id': task_id},
                                status_code=400)
