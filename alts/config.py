import os

# TODO: Use .env file to parse variables from
RABBITMQ_USER = os.environ['RABBITMQ_USER']
RABBITMQ_PASSWORD = os.environ['RABBITMQ_PASSWORD']
RABBITMQ_HOST = os.environ.get('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = int(os.environ.get('RABBITMQ_PORT', 5672))
RABBITMQ_VHOST = os.environ.get('RABBITMQ_VHOST', 'test_system')

ARTIFACTS_ROOT_DIRECTORY = 'test_system_artifacts'


broker_url = f'amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/{RABBITMQ_VHOST}'
# Worker configuration

# Tell Celery to not fetch more than 1 task per worker thread.
# For example, if we have 10 threads (--concurrency=10), with this setting in mind Celery will fetch 10 tasks
# from broker.
worker_prefetch_multiplier = 1
# FIXME: To enable later
result_backend = 's3'

# Tasks configuration
task_track_started = True

# S3 configuration
s3_access_key_id = None
s3_secret_access_key = None
s3_bucket = None
s3_base_path = 'celery_result_backend/'
s3_region = 'eu-north-1'
s3_endpoint_url = None
