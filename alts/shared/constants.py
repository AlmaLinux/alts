from enum import IntEnum

__all__ = [
    'ALPINE_FLAVORS',
    'API_VERSION',
    'ARCHITECTURES',
    'ALLOWED_CHANNELS',
    'COMMAND_TIMEOUT_EXIT_CODE',
    'COSTS',
    'DRIVERS',
    'DEFAULT_FILE_CHUNK_SIZE',
    'DEBIAN_FLAVORS',
    'RHEL_FLAVORS',
    'SUPPORTED_ARCHITECTURES',
    'SUPPORTED_DISTRIBUTIONS',
    'DEFAULT_REQUEST_TIMEOUT',
    'DEFAULT_UPLOADER_CONCURRENCY',
    'DEFAULT_SSH_AUTH_METHODS',
    'X32_ARCHITECTURES',
    'X64_ARCHITECTURES',
]


# YYYYMMDD format for API version
API_VERSION = '20210512'
COSTS = [str(i) for i in range(5)]
ARCHITECTURES = ('x86_64', 'aarch64', 'ppc64le', 's390x')
DRIVERS = ('docker', 'opennebula')
X32_ARCHITECTURES = [
    'i386',
    'i486',
    'i586',
    'i686',
]
X64_ARCHITECTURES = [
    'x86_64',
    'amd64',
    'arm64',
    'aarch64',
    'ppc64le',
]
SUPPORTED_ARCHITECTURES = X32_ARCHITECTURES + X64_ARCHITECTURES + ['s390x']
SUPPORTED_DISTRIBUTIONS = ['almalinux', 'centos', 'ubuntu', 'debian', 'alpine']
RHEL_FLAVORS = [
    'rhel',
    'fedora',
    'centos',
    'almalinux',
]
ALPINE_FLAVORS = ['alpine']
DEBIAN_FLAVORS = ['debian', 'ubuntu', 'raspbian']
ALLOWED_CHANNELS = ['stable', 'beta']


DEFAULT_FILE_CHUNK_SIZE = 8388608  # 8 megabytes in bytes
DEFAULT_REQUEST_TIMEOUT = 300  # 5 minutes
DEFAULT_UPLOADER_CONCURRENCY = 4
# Exit code the same as HTTP request
COMMAND_TIMEOUT_EXIT_CODE = 408

DEFAULT_SSH_AUTH_METHODS = [
    'gssapi-keyex',
    'gssapi-with-mic',
    'hostbased',
    'publickey',
]

ERROR_STRINGS = (
    'Error: Failed to wait virtual machine to be in RUNNING state',
)


class TapStatusEnum(IntEnum):
    FAILED = 0
    DONE = 1
    TODO = 2
    SKIPPED = 3
