import os
import ssl
from logging import Logger
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

from pydantic import BaseModel, ConfigDict

from alts.shared import constants

__all__ = [
    'AsyncSSHParams',
    'CeleryConfig',
    'CommandResult',
    'Repository',
    'SchedulerConfig',
    'TaskRequestResponse',
    'TaskRequestPayload',
    'TaskResultResponse',
]


class Repository(BaseModel):
    name: Optional[str] = None
    baseurl: str


class AsyncSSHParams(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    host: str
    username: Optional[str] = None
    password: Optional[str] = None
    timeout: Optional[int] = None
    client_keys_files: Optional[List[str]] = None
    known_hosts_files: Optional[List[str]] = None
    env_vars: Optional[Dict[str, Any]] = None
    disable_known_hosts_check: bool = False
    ignore_encrypted_keys: bool = False
    keepalive_interval: int = 0
    keepalive_count_max: int = 3
    logger: Optional[Logger] = None
    logger_name: str = 'asyncssh-client'
    logging_level: Literal['DEBUG', 'INFO'] = 'DEBUG'
    preferred_auth: Union[
        str,
        List[str],
    ] = constants.DEFAULT_SSH_AUTH_METHODS


class CommandResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str

    def is_successful(self, expected_exit_code: int = 0) -> bool:
        return self.exit_code == expected_exit_code


class TestConfiguration(BaseModel):
    tests: List[dict] = []
    test_env: Optional[dict] = None


class TaskRequestPayload(BaseModel):
    runner_type: Literal['any', 'docker', 'opennebula'] = 'any'
    dist_name: str
    dist_version: Union[str, int]
    dist_arch: str
    package_channel: Optional[Literal["stable", "beta"]] = None
    test_configuration: Optional[TestConfiguration] = None
    repositories: List[Repository] = []
    package_name: str
    package_version: Optional[str] = None
    module_name: Optional[str] = None
    module_stream: Optional[str] = None
    module_version: Optional[str] = None
    callback_href: Optional[str] = None


class TaskRequestResponse(BaseModel):
    success: bool
    error_description: Optional[str] = None
    task_id: Optional[str] = None
    api_version: str


class TaskResultResponse(BaseModel):
    state: str
    result: Optional[dict] = None


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


class OpennebulaConfig(BaseModel):
    # OpenNebula section
    rpc_endpoint: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    vm_group: Optional[str] = None
    default_vm_disk_size: Optional[int] = 15000
    default_vm_ram_size: Optional[int] = 1024
    network: Optional[str] = None


class RabbitmqBrokerConfig(BaseBrokerConfig):
    use_ssl: bool = False
    ssl_config: Optional[SslConfig] = None
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
        return (
            f'{schema}://{self.rabbitmq_user}:{self.rabbitmq_password}@'
            f'{self.rabbitmq_host}:{port}/{self.rabbitmq_vhost}'
        )


class RedisBrokerConfig(BaseBrokerConfig):
    redis_host: str
    redis_port: int = 6379
    redis_db_number: int = 0
    redis_user: str = 'default'
    redis_password: Optional[str] = None

    @property
    def broker_url(self) -> str:
        if self.redis_user and self.redis_password:
            return (
                f'redis://{self.redis_user}:{self.redis_password}@'
                f'{self.redis_host}:{self.redis_port}/{self.redis_db_number}'
            )
        return (
            f'redis://{self.redis_host}:{self.redis_port}/'
            f'{self.redis_db_number}'
        )


class AzureResultsConfig(BaseResultsConfig):
    azureblockblob_container_name: str
    azureblockblob_base_path: str = 'celery_result_backend/'
    azure_connection_string: str


class FilesystemResultsConfig(BaseResultsConfig):
    path: str


class RedisResultsConfig(BaseResultsConfig, RedisBrokerConfig):
    pass


class S3ResultsConfig(BaseResultsConfig):
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket: str
    s3_base_path: str = 'celery_result_backend/'
    s3_region: str
    s3_endpoint_url: str


class AzureLogsConfig(BaseLogsConfig, AzureResultsConfig):
    azure_logs_container: str


class PulpLogsConfig(BaseLogsConfig):
    pulp_host: str
    pulp_user: str
    pulp_password: str


class CeleryConfig(BaseModel):
    def __init__(self, **data):
        super().__init__(**data)
        # Fill attributes from results config
        for (
            field_name,
            field,
        ) in self.results_backend_config.model_dump().items():
            if field_name == 'broker_url' or field_name.startswith(
                ('s3_', 'azure')
            ):
                setattr(self, field_name, field)

    # Whether to setup Celery SSL
    use_ssl: bool = False
    # SSL configuration section
    ssl_config: Optional[SslConfig] = None
    # Celery configuration variables
    broker_config: Union[RabbitmqBrokerConfig, RedisBrokerConfig]
    opennebula_config: Optional[OpennebulaConfig] = None
    results_backend_config: Union[
        AzureResultsConfig,
        FilesystemResultsConfig,
        RedisResultsConfig,
        S3ResultsConfig,
    ]
    result_backend_always_retry: bool = True
    result_backend_max_retries: int = 10
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_base_path: Optional[str] = None
    s3_region: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    azureblockblob_container_name: Optional[str] = None
    azureblockblob_base_path: str = 'celery_result_backend/'
    azure_connection_string: Optional[str] = None
    task_default_queue: str = 'default'
    task_acks_late: bool = True
    enable_integrity_tests: bool = True
    task_track_started: bool = True
    worker_prefetch_multiplier: int = 1
    broker_pool_limit: int = 20
    # Task track timeout
    task_tracking_timeout: int = 3600
    # Supported architectures and distributions
    supported_architectures: List[str] = constants.SUPPORTED_ARCHITECTURES
    supported_distributions: List[str] = constants.SUPPORTED_DISTRIBUTIONS
    rhel_flavors: List[str] = constants.RHEL_FLAVORS
    debian_flavors: List[str] = constants.DEBIAN_FLAVORS
    supported_runners: Union[List[str], str] = 'all'
    allowed_channel_names: List[str] = constants.ALLOWED_CHANNELS
    gerrit_username: str = ''
    # SSH section
    ssh_public_key_path: str = '~/.ssh/id_rsa.pub'
    # Build system settings
    bs_host: Optional[str] = None
    bs_tasks_endpoint: str = '/api/v1/tests/get_test_tasks/'
    bs_token: Optional[str] = None
    # Log uploader settings
    logs_uploader_config: Optional[
        Union[AzureLogsConfig, PulpLogsConfig]
    ] = None
    uninstall_excluded_pkgs: List[str] = [
        'almalinux-release',
        'kernel',
        'dnf',
    ]
    commands_exec_timeout: int = 30  # unit in seconds

    @property
    def result_backend(self) -> str:
        if isinstance(self.results_backend_config, RedisResultsConfig):
            return self.results_backend_config.broker_url
        if isinstance(self.results_backend_config, AzureResultsConfig):
            con_str = self.results_backend_config.azure_connection_string
            return f'azureblockblob://{con_str}'
        if isinstance(self.results_backend_config, S3ResultsConfig):
            return 's3'
        if isinstance(self.results_backend_config, FilesystemResultsConfig):
            return self.results_backend_config.path
        raise ValueError('Cannot figure out the results backend')

    @property
    def broker_url(self) -> str:
        return self.broker_config.broker_url


class SchedulerConfig(CeleryConfig):
    testing: bool = False
    working_directory: str = '/srv/alts/scheduler'
    jwt_secret: str
    hashing_algorithm: str = 'HS256'
