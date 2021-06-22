from celery import Celery

from alts.shared.utils.path_utils import get_abspath
from alts.worker import CONFIG


__all__ = ['celery_app']


celery_app = Celery('alts', include=['alts.worker.tasks'])
celery_app.config_from_object(CONFIG)
celery_app.conf.update(result_accept_content=['json'])

if CONFIG.use_ssl:
    if not CONFIG.ssl_config:
        raise ValueError('Empty SSL configuration section')

    # TODO: Figure out message signing with actual SSL certificates from CA
    celery_app.conf.update(
        # security_key=get_abspath(CONFIG.ssl_config.security_key),
        # security_certificate=get_abspath(CONFIG.ssl_config.security_certificate),
        # security_cert_store=CONFIG.ssl_config.security_cert_store,
        # security_digest=CONFIG.ssl_config.security_digest,
        # task_serializer='auth',
        # event_serializer='auth',
        # accept_content=['auth'],
        broker_use_ssl={
            'keyfile': get_abspath(CONFIG.ssl_config.security_key),
            'certfile': get_abspath(CONFIG.ssl_config.security_certificate),
            'ca_certs': get_abspath(CONFIG.ssl_config.broker_ca_certificates),
            'cert_reqs': CONFIG.ssl_config.cert_required
        },
    )
    # celery_app.setup_security()

celery_app.autodiscover_tasks()
