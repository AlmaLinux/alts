# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System tasks scheduler connection to the database."""

import logging
import os
import time

import sqlalchemy
from databases import Database
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from alts.scheduler import CONFIG, DATABASE_NAME


__all__ = ['database', 'Session', 'Task', 'prune_tasks']


if not os.path.exists(CONFIG.working_directory):
    os.makedirs(CONFIG.working_directory)


database_path = os.path.join(CONFIG.working_directory, DATABASE_NAME)
database_url = f'sqlite:///{database_path}'
database = Database(database_url)
engine = sqlalchemy.engine.create_engine(
    database_url, connect_args={"check_same_thread": False}
)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class Queue(Base):

    """Test System database tasks' queues table."""

    __tablename__ = 'queues'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String, unique=True)
    cost = sqlalchemy.Column(sqlalchemy.Integer)


class Task(Base):

    """Test System database tasks table."""

    __tablename__ = 'tasks'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    task_id = sqlalchemy.Column(sqlalchemy.String, unique=True)
    bs_task_id = sqlalchemy.Column(sqlalchemy.String)
    callback_href = sqlalchemy.Column(sqlalchemy.String)
    queue_name = sqlalchemy.Column(sqlalchemy.String)
    status = sqlalchemy.Column(sqlalchemy.String)
    task_duration = sqlalchemy.Column(sqlalchemy.String, nullable=True)

    def __str__(self):
        """
        Converts task identifier and task status to string task information.

        Returns
        -------
        str
            Formatted specified task information.
        """
        return f'Task: task ID {self.task_id}, status {self.status}'

    def __repr__(self):
        """
        Gets task info as a string.

        Returns
        -------
        str
            Formatted specified task information.
        """
        return self.__str__()


Base.metadata.create_all(engine)


def prune_tasks(
    keep_rows: int = None,
    chunk_size: int = None,
    time_budget: int = None,
) -> int:
    """
    Drops task bookkeeping rows outside the most recent `keep_rows` window.

    Rows are only needed to map a build system task to its Celery task while
    it can still be cancelled, so anything well behind the newest row is dead
    weight. Deletion runs in chunks under a wall clock budget: the first run
    against a table that has grown for a long time would otherwise block
    startup and hold a long write transaction. Whatever is left over is
    picked up by the next run.

    Parameters
    ----------
    keep_rows : int
        How many of the most recent rows to keep.
    chunk_size : int
        How many rows to delete per transaction.
    time_budget : int
        How long to spend deleting before giving up until next startup
        (in seconds).

    Returns
    -------
    int
        Number of rows deleted.
    """
    keep_rows = (
        CONFIG.task_retention_rows if keep_rows is None else keep_rows
    )
    chunk_size = (
        CONFIG.task_prune_chunk_size if chunk_size is None else chunk_size
    )
    time_budget = (
        CONFIG.task_prune_time_budget if time_budget is None else time_budget
    )
    if keep_rows <= 0:
        return 0

    tasks_table = Task.__table__
    deleted = 0
    deadline = time.monotonic() + time_budget
    try:
        with engine.connect() as conn:
            max_id = conn.execute(
                sqlalchemy.select(sqlalchemy.func.max(tasks_table.c.id))
            ).scalar()
        if max_id is None:
            return 0
        watermark = max_id - keep_rows
        if watermark <= 0:
            return 0

        doomed = sqlalchemy.select(tasks_table.c.id).where(
            tasks_table.c.id <= watermark
        ).limit(chunk_size)
        while time.monotonic() < deadline:
            with engine.begin() as conn:
                removed = conn.execute(
                    tasks_table.delete().where(tasks_table.c.id.in_(doomed))
                ).rowcount
            deleted += removed
            if removed < chunk_size:
                break
        else:
            logging.warning(
                'Task pruning hit its %ss budget with rows still at or below '
                'id %s; the rest will be removed on the next startup',
                time_budget,
                watermark,
            )
    except Exception:
        logging.exception('Cannot prune old task records:')
        return deleted

    if deleted:
        logging.info(
            'Pruned %s task records at or below id %s, keeping the most '
            'recent %s',
            deleted,
            watermark,
            keep_rows,
        )
    return deleted
