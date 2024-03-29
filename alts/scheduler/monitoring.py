import logging
import random
import threading
import time

from celery.contrib.abortable import AbortableAsyncResult
from celery.exceptions import TimeoutError
from celery.states import READY_STATES

from alts.scheduler.db import Session, Task


class TasksMonitor(threading.Thread):
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

    def run(self) -> None:
        while (
            not self.__graceful_terminate.is_set()
            or not self.__terminated_event.is_set()
        ):

            updated_tasks = []
            with Session() as session, session.begin():
                for task in session.query(Task).filter(
                    Task.status.notin_(READY_STATES),
                    Task.status != 'ABORTED',
                ):
                    task_result = AbortableAsyncResult(task.task_id, app=self.__celery)
                    # Ensure that task state will be updated
                    # by getting task result
                    try:
                        _ = task_result.get(timeout=self.__get_result_timeout)
                        self.logger.debug(f"Current status of {task.task_id} is {task_result.state}")
                    except TimeoutError:
                        pass
                    except Exception as e:
                        self.logger.warning(
                            'Non-critical error in acquiring task result: %s',
                            str(e)
                        )
                    if task_result.state != task.status:
                        self.logger.info(
                            'Updating task %s status to %s',
                            task.task_id,
                            task_result.state,
                        )
                        task.status = task_result.state
                        updated_tasks.append(task)
                    time.sleep(0.5)
                if updated_tasks:
                    try:
                        session.add_all(updated_tasks)
                        session.commit()
                    except Exception:
                        self.logger.exception('Cannot update tasks statuses:')
            self.__terminated_event.wait(random.randint(10, 15))
