import logging
import random
import threading
import time

from celery.exceptions import TimeoutError
from celery.states import READY_STATES

from alts.scheduler.db import Session, Task


class TasksMonitor(threading.Thread):
    def __init__(self, terminated_event: threading.Event,
                 graceful_terminate: threading.Event, celery_app,
                 get_result_timeout: int = 1):
        super().__init__()
        self.__terminated_event = terminated_event
        self.__graceful_terminate = graceful_terminate
        self.__celery = celery_app
        self.__get_result_timeout = get_result_timeout
        self.logger = logging.getLogger(__file__)

    def run(self) -> None:
        while not self.__graceful_terminate.is_set() or \
                not self.__terminated_event.is_set():
            session = Session()
            updated_tasks = []
            for task in session.query(Task).filter(
                    Task.status.notin_(READY_STATES)):
                task_result = self.__celery.AsyncResult(task.task_id)
                # Ensure that task state will be updated
                # by getting task result
                try:
                    _ = task_result.get(timeout=self.__get_result_timeout)
                except TimeoutError:
                    pass
                if task_result.state != task.status:
                    self.logger.info(f'Updating task {task.task_id} status '
                                     f'to {task_result.state}')
                    task.status = task_result.state
                    updated_tasks.append(task)
                time.sleep(0.5)

            if updated_tasks:
                try:
                    session.add_all(updated_tasks)
                    session.commit()
                except Exception as e:
                    self.logger.error(f'Cannot update tasks statuses: {e}')
                    session.rollback()
            session.close()
            self.__terminated_event.wait(random.randint(5, 10))
