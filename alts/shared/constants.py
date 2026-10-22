import re

__all__ = [
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
    'MIRROR_ERROR_SIGNATURES',
    'MIRROR_RETRY_DELAYS',
    'MIRROR_RETRY_JITTER',
    'X32_ARCHITECTURES',
    'X64_ARCHITECTURES',
    'TESTS_MAPPING',
]


# YYYYMMDD format for API version
API_VERSION = '20210512'
COSTS = [str(i) for i in range(5)]
ARCHITECTURES = (
    'x86_64',
    'x86_64_v2',
    'aarch64',
    'ppc64le',
    's390x',
)
DRIVERS = ('docker', 'opennebula')
X32_ARCHITECTURES = [
    'i386',
    'i486',
    'i586',
    'i686',
]
X64_ARCHITECTURES = [
    'x86_64',
    'x86_64_v2',
    'amd64',
    'arm64',
    'aarch64',
    'ppc64le',
]
SUPPORTED_ARCHITECTURES = X32_ARCHITECTURES + X64_ARCHITECTURES + ['s390x']
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
# Exit code the same as HTTP request
COMMAND_TIMEOUT_EXIT_CODE = 408

# Output of dnf, yum and apt when a repository or a mirror is temporarily
# unavailable (network trouble, a mirror in the middle of a sync). Such
# failures are not caused by the tested packages, and the same command
# usually succeeds a few seconds later.
MIRROR_ERROR_SIGNATURES = tuple(
    re.compile(signature, re.IGNORECASE)
    for signature in (
        # dnf
        r'Cannot download repomd\.xml',
        r'Curl error \(\d+\)',
        r'Loading repository .* has failed',
        r'all mirrors were (already )?tried',
        r'Failed to download metadata for repo',
        r'Errors during downloading metadata for repository',
        r'Failed to download packages',
        r'Failed to synchronize cache for repo',
        r'Cannot prepare internal mirrorlist',
        r'Status code: 5\d\d',
        r'Timeout was reached',
        # yum
        r'Cannot retrieve repository metadata',
        r'Cannot find a valid baseurl for repo',
        r'No more mirrors to try',
        r'\[Errno 14\]',
        # apt
        r'Failed to fetch',
        r'Hash Sum mismatch',
        r'Temporary failure resolving',
    )
)
# Seconds to wait before each retry of a package manager operation that
# failed on a mirror error. The operation runs at most
# len(MIRROR_RETRY_DELAYS) + 1 times.
MIRROR_RETRY_DELAYS = (5, 10, 15)
# Each delay is randomly stretched or shrunk by up to this share, so that
# the many test tasks a mirror failed at once don't all retry at once
# (on average the delays still add up to 30 seconds)
MIRROR_RETRY_JITTER = 0.25

DEFAULT_SSH_AUTH_METHODS = [
    'gssapi-keyex',
    'gssapi-with-mic',
    'hostbased',
    'publickey',
]

TESTS_MAPPING = {
    'run_system_info_commands': 'system_info',
    'install_package': 'install_package',
    'run_package_integrity_tests': 'package_integrity_tests',
    'run_third_party_tests': 'third_party_tests',
    'uninstall_package': 'uninstall_package',
}
