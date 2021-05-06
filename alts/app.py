from celery import Celery

from alts import CONFIG


__all__ = ['celery_app']


celery_app = Celery('alts', include=['alts.tasks'])
celery_app.config_from_object(CONFIG)
celery_app.conf.update(
    task_routes={
        'alts.tasks.run_docker': {'queue': 'docker-x86_64-0'},
    },
    result_accept_content=['json']
)
celery_app.autodiscover_tasks()
