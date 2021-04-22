from celery import Celery


__all__ = ['celery_app']


celery_app = Celery('alts', include=['alts.tasks'])
celery_app.config_from_object('alts.config')
celery_app.autodiscover_tasks()
