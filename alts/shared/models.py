import os
import ssl
import typing

import jmespath
from pydantic import BaseModel, ValidationError, validator

from alts.shared.constants import DRIVERS


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
    callback_href: str = None

    @validator('runner_type')
    def validate_runner_type(cls, value: str) -> str:
        # TODO: Add config or constant to have all possible runner types
        runner_types = DRIVERS + ('any',)
        if value not in runner_types:
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


class SslConfig(BaseModel):
    security_key: str
    security_certificate: str
    security_digest: str = 'sha256'
    broker_ca_certificates: str
    cert_required: int = ssl.CERT_REQUIRED

    @property
    def security_cert_store(self) -> str:
        # FIXME: Find correct way to search for certs store
        search_folder = '/etc/ssl/certs/'
        for file_ in os.listdir(search_folder):
            if file_.startswith('ca-') and '.trust' not in file_:
                return os.path.join(search_folder, file_)
        raise ValueError('Cannot find SSL certificates file')


class CeleryConfig(BaseModel):
    # Needed for broker_url property
    use_ssl: bool = False
    ssl_config: typing.Optional[SslConfig] = None
    rabbitmq_host: str
    rabbitmq_port: int = 5672
    rabbitmq_ssl_port: int = 5671
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_vhost: str
    # Celery configuration variables
    result_backend: str
    result_backend_always_retry: bool = True
    result_backend_max_retries: int = 10
    s3_access_key_id: str = ''
    s3_secret_access_key: str = ''
    s3_bucket: str = ''
    s3_base_path: str = 'celery_result_backend/'
    s3_region: str = ''
    s3_endpoint_url: typing.Optional[str] = None
    azureblockblob_container_name: str
    azureblockblob_base_path: str = 'celery_result_backend/'
    azure_connection_string: str
    azure_logs_container: str
    task_default_queue: str = 'default'
    task_acks_late: bool = True
    task_track_started: bool = True
    artifacts_root_directory: str = 'alts_artifacts'
    worker_prefetch_multiplier: int = 1
    broker_pool_limit: int = 20
    # Task track timeout
    task_tracking_timeout: int = 3600
    # Supported architectures and distributions
    supported_architectures: typing.List[str] = ['x86_64', 'i686', 'amd64',
                                                 'arm64', 'aarch64', 'ppc64le']
    supported_distributions: typing.List[str] = ['almalinux', 'centos',
                                                 'ubuntu', 'debian']
    rhel_flavors: typing.Tuple[str] = ('fedora', 'centos', 'almalinux',
                                       'cloudlinux')
    debian_flavors: typing.Tuple[str] = ('debian', 'ubuntu', 'raspbian')
    supported_runners: typing.Union[typing.List[str], str] = 'all'
    # OpenNebula section
    opennebula_rpc_endpoint: str = ''
    opennebula_username: str = ''
    opennebula_password: str = ''
    opennebula_vm_group: str = ''
    opennebula_templates: dict = {}
    # SSH section
    ssh_public_key_path: str = '~/.ssh/id_rsa.pub'
    # Build system settings
    bs_host: str
    bs_token: str

    @property
    def broker_url(self) -> str:
        if self.use_ssl:
            schema = 'amqps'
            port = self.rabbitmq_ssl_port
        else:
            schema = 'amqp'
            port = self.rabbitmq_port
        return (f'{schema}://{self.rabbitmq_user}:{self.rabbitmq_password}@'
                f'{self.rabbitmq_host}:{port}/{self.rabbitmq_vhost}')

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
