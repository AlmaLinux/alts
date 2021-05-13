import typing

import jmespath
from pydantic import BaseModel, ValidationError, validator


__all__ = ['CeleryConfig', 'Repository', 'SchedulerConfig',
           'TaskRequestResponse', 'TaskRequestPayload', 'TaskResultResponse']


class Repository(BaseModel):
    name: typing.Optional[str] = None
    baseurl: str


class TaskRequestPayload(BaseModel):
    runner_type: str = 'any'
    dist_name: str
    dist_version: typing.Union[str, int]
    dist_arch: str
    repositories: typing.List[Repository] = []
    package_name: str
    package_version: typing.Optional[str] = None
    callback_url: str = None

    @validator('runner_type')
    def validate_runner_type(cls, value: str) -> str:
        # TODO: Add config or constant to have all possible runner types
        if value not in ('any', 'docker', 'opennebula'):
            raise ValidationError(f'Unknown runner type: {value}')
        return value


class TaskRequestResponse(BaseModel):
    success: bool
    error_description: typing.Optional[str] = None
    task_id: typing.Optional[str] = None
    api_version: str


class TaskResultResponse(BaseModel):
    state: str
    result: typing.Optional[dict]


class CeleryConfig(BaseModel):
    # Needed for broker_url property
    rabbitqm_host: str
    rabbitmq_port: int = 5672
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_vhost: str
    result_backend: str
    # Celery configuration variables
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
    # Task track timeout
    task_tracking_timeout: int = 3600
    # Supported architectures and distributions
    supported_architectures: typing.List[str] = ['x86_64', 'i686', 'amd64',
                                                 'arm64', 'aarch64']
    supported_distributions: typing.List[str] = ['almalinux', 'centos',
                                                 'ubuntu', 'debian']
    supported_runners: typing.Union[typing.List[str], str] = 'all'
    # OpenNebula section
    opennebula_rpc_endpoint: str = ''
    opennebula_username: str = ''
    opennebula_password: str = ''
    opennebula_vm_group: str = ''
    opennebula_templates: dict = {}
    # SSH section
    ssh_public_key_path: str = '~/.ssh/id_rsa.pub'

    @property
    def broker_url(self) -> str:
        return (f'amqp://{self.rabbitmq_user}:{self.rabbitmq_password}@'
                f'{self.rabbitqm_host}:{self.rabbitmq_port}/'
                f'{self.rabbitmq_vhost}')

    def get_opennebula_template_id(self, dist_name: str, dist_version: str,
                                   dist_arch: str):
        template_id_path = f'{dist_name}."{dist_version}"."{dist_arch}"'
        template_id = jmespath.search(
            template_id_path, self.opennebula_templates)
        if not template_id:
            raise KeyError(f'Nothing found for {template_id_path}')
        return template_id


class SchedulerConfig(CeleryConfig):
    testing: bool = False
    working_directory: str = '/srv/alts/scheduler'
    jwt_secret: str
    hashing_algorithm: str = 'HS256'
