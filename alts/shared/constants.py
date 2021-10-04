
__all__ = ['API_VERSION', 'ARCHITECTURES', 'COSTS', 'DRIVERS']


# YYYYMMDD format for API version
API_VERSION = '20210512'
COSTS = [str(i) for i in range(5)]
ARCHITECTURES = ('x86_64', 'aarch64', 'ppc64le')
DRIVERS = ('docker', 'opennebula')
SUPPORTED_ARCHITECTURES = ['x86_64', 'i686', 'amd64', 'arm64', 'aarch64',
                           'ppc64le']
SUPPORTED_DISTRIBUTIONS = ['almalinux', 'centos', 'ubuntu', 'debian']
RHEL_FLAVORS = ('rhel', 'fedora', 'centos', 'almalinux', 'cloudlinux')
DEBIAN_FLAVORS = ('debian', 'ubuntu', 'raspbian')
