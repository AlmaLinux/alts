import itertools
import logging

from celery import Celery
from kombu import Exchange, Queue

from alts.shared.constants import ARCHITECTURES, COSTS, DRIVERS
from alts.shared.utils.path_utils import get_abspath
from alts.worker import CONFIG


__all__ = ['celery_app']


# Disable too verbose logging from Azure SDK
logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(
    'WARNING')
celery_app = Celery('alts', include=['alts.worker.tasks'])
celery_app.config_from_object(CONFIG)
celery_app.conf.update(result_accept_content=['json'])

# Define all queues so client (scheduler) would be aware of all of them
task_queues = [Queue('default', Exchange('default', type='direct'),
                     routing_key='default')]
for queue_tuple in itertools.product(DRIVERS, ARCHITECTURES, COSTS):
    queue_name = '-'.join(queue_tuple)
    task_queues.append(Queue(queue_name, Exchange(queue_name, type='direct'),
                             routing_key=queue_name))

celery_app.conf.task_queues = task_queues
celery_app.conf.task_default_queue = 'default'
celery_app.conf.task_default_exchange = 'default'
celery_app.conf.task_default_routing_key = 'default'

if CONFIG.use_ssl:
    if not getattr(CONFIG, 'ssl_config'):
        raise ValueError('Empty SSL configuration section')

    # TODO: Figure out message signing with actual SSL certificates from CA
    celery_app.conf.update(
        broker_use_ssl={
            'keyfile': get_abspath(CONFIG.ssl_config.security_key),
            'certfile': get_abspath(CONFIG.ssl_config.security_certificate),
            'ca_certs': get_abspath(CONFIG.ssl_config.broker_ca_certificates),
            'cert_reqs': CONFIG.ssl_config.cert_required
        },
    )

celery_app.autodiscover_tasks()
