import os

import sqlalchemy
from databases import Database
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from alts.scheduler import CONFIG, DATABASE_NAME


__all__ = ['database', 'Session', 'Task']


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
    __tablename__ = 'queues'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String, unique=True)
    cost = sqlalchemy.Column(sqlalchemy.Integer)


class Task(Base):
    __tablename__ = 'tasks'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    task_id = sqlalchemy.Column(sqlalchemy.String, unique=True)
    queue_name = sqlalchemy.Column(sqlalchemy.String)
    status = sqlalchemy.Column(sqlalchemy.String)
    task_duration = sqlalchemy.Column(sqlalchemy.String, nullable=True)

    def __str__(self):
        return f'Task: task ID {self.task_id}, status {self.status}'

    def __repr__(self):
        return self.__str__()


Base.metadata.create_all(engine)
