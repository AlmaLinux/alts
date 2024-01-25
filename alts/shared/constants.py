__all__ = [
    'API_VERSION',
    'ARCHITECTURES',
    'ALLOWED_CHANNELS',
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
]


# YYYYMMDD format for API version
API_VERSION = '20210512'
COSTS = [str(i) for i in range(5)]
ARCHITECTURES = ('x86_64', 'aarch64', 'ppc64le', 's390x')
DRIVERS = ('docker', 'opennebula')
SUPPORTED_ARCHITECTURES = [
    'x86_64',
    'i686',
    'amd64',
    'arm64',
    'aarch64',
    'ppc64le',
    's390x',
]
SUPPORTED_DISTRIBUTIONS = ['almalinux', 'centos', 'ubuntu', 'debian']
RHEL_FLAVORS = [
    'rhel',
    'fedora',
    'centos',
    'almalinux',
]
DEBIAN_FLAVORS = ['debian', 'ubuntu', 'raspbian']
ALLOWED_CHANNELS = ['stable', 'beta']


DEFAULT_FILE_CHUNK_SIZE = 8388608  # 8 megabytes in bytes
DEFAULT_REQUEST_TIMEOUT = 60  # 1 minute
DEFAULT_UPLOADER_CONCURRENCY = 4

DEFAULT_SSH_AUTH_METHODS = [
    'gssapi-keyex',
    'gssapi-with-mic',
    'hostbased',
    'publickey',
]
