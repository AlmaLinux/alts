import sqlalchemy
from databases import Database
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from scheduler.config import DATABASE_URL


__all__ = ['database', 'Session', 'Task']


database = Database(DATABASE_URL)
engine = sqlalchemy.engine.create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class Queue(Base):
    __tablename__ = 'queues'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String, unique=True)
    cost = sqlalchemy.Column(sqlalchemy.Integer)
    max_capacity = sqlalchemy.Column(sqlalchemy.Integer)


class Task(Base):
    __tablename__ = 'tasks'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    task_id = sqlalchemy.Column(sqlalchemy.String, unique=True)
    # queue_name = sqlalchemy.Column(sqlalchemy.ForeignKey('queue.name'))
    queue_name = sqlalchemy.Column(sqlalchemy.String)
    status = sqlalchemy.Column(sqlalchemy.String)
    task_duration = sqlalchemy.Column(sqlalchemy.String, nullable=True)

    # queue = sqlalchemy.orm.relationship('Queue', back_populates='tasks')


Base.metadata.create_all(engine)
