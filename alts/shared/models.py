import os
import ssl
import typing

from pydantic import BaseModel, ValidationError, validator

from alts.shared import constants


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
    module_name: typing.Optional[str] = None
    module_stream: typing.Optional[str] = None
    module_version: typing.Optional[str] = None
    callback_href: str = None

    @validator('runner_type')
    def validate_runner_type(cls, value: str) -> str:
        # TODO: Add config or constant to have all possible runner types
        runner_types = constants.DRIVERS + ('any',)
        if value not in runner_types:
            raise ValidationError(f'Unknown runner type: {value}', cls)
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


class BaseBrokerConfig(BaseModel):
    @property
    def broker_url(self) -> str:
        raise NotImplementedError()


class BaseLogsConfig(BaseModel):
    uploader_concurrency: int = constants.DEFAULT_UPLOADER_CONCURRENCY
    artifacts_root_directory: str = 'alts_artifacts'
    skip_artifacts_upload: bool = False


class BaseResultsConfig(BaseModel):
    pass


class RabbitmqBrokerConfig(BaseBrokerConfig):
    use_ssl: bool = False
    ssl_config: typing.Optional[SslConfig] = None
    rabbitmq_host: str
    rabbitmq_port: int = 5672
    rabbitmq_ssl_port: int = 5671
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_vhost: str

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


class RedisBrokerConfig(BaseBrokerConfig):
    redis_host: str
    redis_port: int = 6379
    redis_db_number: int = 0
    redis_user: str = 'default'
    redis_password: typing.Optional[str]

    @property
    def broker_url(self) -> str:
        if self.redis_user and self.redis_password:
            return (
                f'redis://{self.redis_user}:{self.redis_password}@'
                f'{self.redis_host}:{self.redis_port}/{self.redis_db_number}'
            )
        return (f'redis://{self.redis_host}:{self.redis_port}/'
                f'{self.redis_db_number}')


class AzureResultsConfig(BaseResultsConfig):
    azureblockblob_container_name: typing.Optional[str]
    azureblockblob_base_path: str = 'celery_result_backend/'
    azure_connection_string: typing.Optional[str]


class FilesystemResultsConfig(BaseResultsConfig):
    path: typing.Optional[str]


class RedisResultsConfig(BaseResultsConfig, RedisBrokerConfig):
    pass


class S3ResultsConfig(BaseResultsConfig):
    s3_access_key_id: typing.Optional[str]
    s3_secret_access_key: typing.Optional[str]
    s3_bucket: typing.Optional[str]
    s3_base_path: str = 'celery_result_backend/'
    s3_region: typing.Optional[str]
    s3_endpoint_url: typing.Optional[str] = None


class AzureLogsConfig(BaseLogsConfig, AzureResultsConfig):
    azure_logs_container: typing.Optional[str]


class PulpLogsConfig(BaseLogsConfig):
    pulp_host: typing.Optional[str]
    pulp_user: typing.Optional[str]
    pulp_password: typing.Optional[str]


class CeleryConfig(BaseModel):
    def __init__(self, **data):
        super().__init__(**data)
        # Fill attributes from results config
        for field_name, field in self.results_backend_config.__fields__.items():
            if (field_name == 'broker_url' or
                    field_name.startswith(('s3_', 'azure'))):
                setattr(self, field_name, field)

    # Whether to setup Celery SSL
    use_ssl: bool = False
    # Celery configuration variables
    broker_config: typing.Union[RabbitmqBrokerConfig, RedisBrokerConfig]
    results_backend_config: typing.Union[
        AzureResultsConfig, FilesystemResultsConfig, RedisResultsConfig,
        S3ResultsConfig
    ]
    result_backend_always_retry: bool = True
    result_backend_max_retries: int = 10
    s3_access_key_id: typing.Optional[str]
    s3_secret_access_key: typing.Optional[str]
    s3_bucket: typing.Optional[str]
    s3_base_path: typing.Optional[str]
    s3_region: typing.Optional[str]
    s3_endpoint_url: typing.Optional[str] = None
    azureblockblob_container_name: typing.Optional[str]
    azureblockblob_base_path: str = 'celery_result_backend/'
    azure_connection_string: typing.Optional[str]
    task_default_queue: str = 'default'
    task_acks_late: bool = True
    task_track_started: bool = True
    worker_prefetch_multiplier: int = 1
    broker_pool_limit: int = 20
    # Task track timeout
    task_tracking_timeout: int = 3600
    # Supported architectures and distributions
    supported_architectures: typing.List[str] = constants.SUPPORTED_ARCHITECTURES
    supported_distributions: typing.List[str] = constants.SUPPORTED_DISTRIBUTIONS
    rhel_flavors: typing.Tuple[str] = constants.RHEL_FLAVORS
    debian_flavors: typing.Tuple[str] = constants.DEBIAN_FLAVORS
    supported_runners: typing.Union[typing.List[str], str] = 'all'
    # OpenNebula section
    opennebula_rpc_endpoint: typing.Optional[str]
    opennebula_username: typing.Optional[str]
    opennebula_password: typing.Optional[str]
    opennebula_vm_group: typing.Optional[str]
    # SSH section
    ssh_public_key_path: str = '~/.ssh/id_rsa.pub'
    # Build system settings
    bs_host: typing.Optional[str]
    bs_token: typing.Optional[str]
    # Log uploader settings
    logs_uploader_config: typing.Union[AzureLogsConfig, PulpLogsConfig]
    uninstall_excluded_pkgs: typing.List[str] = ['almalinux-release', 'kernel', 'dnf']

    @property
    def result_backend(self) -> str:
        if isinstance(self.results_backend_config, RedisResultsConfig):
            return self.results_backend_config.broker_url
        elif isinstance(self.results_backend_config, AzureResultsConfig):
            con_str = self.results_backend_config.azure_connection_string
            return f'azureblockblob://{con_str}'
        elif isinstance(self.results_backend_config, S3ResultsConfig):
            return 's3'
        elif isinstance(self.results_backend_config, FilesystemResultsConfig):
            return self.results_backend_config.path
        else:
            raise ValueError('Cannot figure out the results backend')

    @property
    def broker_url(self) -> str:
        return self.broker_config.broker_url

    def get_opennebula_template_id(self, dist_name: str, dist_version: str,
                                   dist_arch: str):
        # TODO: Remove the method, for now leave the placeholder
        return ''


class SchedulerConfig(CeleryConfig):
    testing: bool = False
    working_directory: str = '/srv/alts/scheduler'
    jwt_secret: str
    hashing_algorithm: str = 'HS256'
