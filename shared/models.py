import typing

from pydantic import BaseModel, ValidationError, validator


__all__ = ['CeleryConfig', 'Repository', 'SchedulerConfig',
           'TaskRequestResponse', 'TaskRequestPayload', 'TaskResultResponse']


class Repository(BaseModel):
    name: typing.Optional[str] = None
    baseurl = str


class TaskRequestPayload(BaseModel):
    runner_type: str
    dist_name: str
    dist_version: typing.Union[str, int]
    dist_arch: str
    repositories: typing.List[Repository] = []
    package_name: str
    package_version: typing.Optional[str] = None

    @validator('runner_type')
    def validate_runner_type(cls, value: str) -> str:
        # TODO: Add config or constant to have all possible runner types
        if value not in ('any', 'docker'):
            raise ValidationError(f'Unknown runner type: {value}')
        return value


class TaskRequestResponse(BaseModel):
    success: bool
    error_description: typing.Optional[str] = None
    task_id: typing.Optional[str] = None


class TaskResultResponse(BaseModel):
    state: str
    result: typing.Optional[dict]


class CeleryConfig(BaseModel):
    rabbitqm_host: str
    rabbitmq_port: int = 5672
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_vhost: str
    result_backend: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket: str
    s3_base_path: str = 'celery_result_backend/'
    s3_region: str
    s3_endpoint_url: typing.Optional[str] = None
    task_default_queue: str = 'default'
    task_acks_late: bool = True
    task_track_started: bool = True
    artifacts_root_directory: str = 'test_system_artifacts'
    worker_prefetch_multiplier: int = 1

    @property
    def broker_url(self) -> str:
        return (f'amqp://{self.rabbitmq_user}:{self.rabbitmq_password}@'
                f'{self.rabbitqm_host}:{self.rabbitmq_port}/'
                f'{self.rabbitmq_vhost}')


class SchedulerConfig(CeleryConfig):
    testing: bool = False
    working_directory: str = '/srv/alts/scheduler'
    jwt_secret: str
    hashing_algorithm: str = 'HS256'
