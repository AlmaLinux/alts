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
    'BS_DEFAULT_PRIORITY',
    'BS_MAX_RULE_PRIORITY',
    'BS_RELEASE_BUILD_PRIORITY',
    'EMERGENCY_TASK_LEVEL',
    'PRIORITY_LEVEL_BOUNDS',
    'RELEASE_BUILD_TOP_LEVEL',
    'TASK_PRIORITY_LEVELS',
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


# The build system sends a test task priority from 0 to 31, higher is more
# urgent (PF-4420). Its priority weights and defaults assign 0..23, the
# levels above are set by hand (EMERGENCY).
BS_MAX_RULE_PRIORITY = 23
# Used when the build system sends no priority: the git tag build default
# for release builds, MEDIUM for the others
BS_RELEASE_BUILD_PRIORITY = 2
BS_DEFAULT_PRIORITY = 1
# A test task level, higher is more urgent. Level N (N >= 1) starts at the
# build system priority PRIORITY_LEVEL_BOUNDS[N - 1], so the values the
# build system gives keep their order: 0 is the low_priority_build tag, 1-2
# MEDIUM and the git tag default, 3-4 and 5-6 routine work on weighted
# platforms, 7, 11, 15 and 19-22 important projects by OS weight, and 23 the
# top weighted priority of a release build and the high_priority_build tag.
# These are the levels before the release build step below, which moves
# e.g. the git tag default of a release build to level 2.
# EMERGENCY_TASK_LEVEL is above BS_MAX_RULE_PRIORITY. Redis serves
# priorities 0..9 only, so there are at most 10 levels.
PRIORITY_LEVEL_BOUNDS = (1, 3, 5, 7, 11, 15, 19, 23)
TASK_PRIORITY_LEVELS = len(PRIORITY_LEVEL_BOUNDS) + 2
EMERGENCY_TASK_LEVEL = TASK_PRIORITY_LEVELS - 1
# The build system gives a release (git tag) build one more than a
# development build with the same weights, but neighbouring priorities can
# share a level, so the tests of a release build also move one level up,
# never to EMERGENCY_TASK_LEVEL. Level 0 is kept: only the
# low_priority_build tag gives it. A level spans 2-4 build system
# priorities, so a release build's tests also go ahead of development tests
# whose priority is up to 3 higher in the same level (release 3 is ahead of
# development 4): release work comes first within a level by design.
RELEASE_BUILD_TOP_LEVEL = EMERGENCY_TASK_LEVEL - 1
