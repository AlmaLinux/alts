import os
import sys

from celery import Celery

sys.path.append(os.path.join(os.path.dirname(__file__), 'alts'))

app = Celery('alts', include=['alts.tasks'])
app.config_from_object('config')
app.autodiscover_tasks()
