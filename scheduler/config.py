import os


def str_to_bool(string: str) -> bool:
    return string.lower() in ('1', 'true')


TESTING = str_to_bool(os.environ.get('TESTING', 'true'))

if TESTING:
    SCHEDULER_WORK_DIR = os.path.abspath(os.path.expanduser(
        '~/projects/data/alts/scheduler'))
else:
    SCHEDULER_WORK_DIR = '/srv/alts/scheduler'

if not os.path.exists(SCHEDULER_WORK_DIR):
    os.makedirs(SCHEDULER_WORK_DIR)

DATABASE_URL = f'sqlite:///{SCHEDULER_WORK_DIR}/scheduler.db'
