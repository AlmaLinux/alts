import re
from collections import namedtuple
from typing import List, Union

from testinfra.host import Host
from testinfra.modules.file import GNUFile
from testinfra.modules.package import DebianPackage, RpmPackage


__all__ = [
    'get_package_files', 'is_package_empty', 'is_file_dynamically_linked',
    'has_missing_shared_libraries', 'resolve_symlink', 'MissingSOResult'
]


MissingSOResult = namedtuple('MissingSOResult', ('missing', 'output'))


def get_package_files(pkg: Union[DebianPackage, RpmPackage]) -> List[str]:
    """
    Returns a list of files inside the package.

    Parameters
    ----------
    pkg: DebianPackage | RpmPackage
        Package instance

    Returns
    -------
    list
        A list of all files from the package

    """
    if isinstance(pkg, DebianPackage):
        command = f'dpkg -L {pkg.name}'
    elif isinstance(pkg, RpmPackage):
        command = f'rpm -ql {pkg.name}'
    else:
        raise ValueError(f'Unknown package type: {type(pkg)}')

    assert pkg.is_installed
    output = pkg.run(command)
    assert output.rc == 0
    return [item for item in output.stdout.strip().split('\n')
            if item and 'contains no files' not in item]


def is_package_empty(pkg: Union[DebianPackage, RpmPackage]) -> bool:
    """
    Checks if package contains no files

    Parameters
    ----------
    pkg: DebianPackage | RpmPackage
        Package instance

    Returns
    -------
    bool
        True if package is empty, False otherwise

    """
    return not bool(get_package_files(pkg))


def is_file_dynamically_linked(file_: GNUFile) -> bool:
    """
    Returns if file is dynamically linked

    Parameters
    ----------
    file_: GNUFile

    Returns
    -------
    bool

    """
    output = file_.run(f'file -b {file_.path}')
    assert output.rc == 0
    return bool(re.search(r'ELF.*?dynamically linked', output.stdout))


def resolve_symlink(host: Host, file_: Union[str, GNUFile],
                    resolve_depth: int = 20) -> str:
    """
    Resolve symlink until actual file or fail in case if symlink is broken

    Parameters
    ----------
    host:           Host

    file_:          str | GNUFile
        Input symlink to resolve
    resolve_depth:  int
        The depth of symlink resolution (failsafe if symlink is circular)

    Returns
    -------
    GNUFile
        Path to the resolved file

    """
    resolve_items = []
    if isinstance(file_, GNUFile):
        initial_file = file_.path
        new_file = file_
    else:
        initial_file = file_
        new_file = host.file(file_)

    while new_file.exists and new_file.is_symlink and resolve_depth > 0:
        new_path = new_file.linked_to
        resolve_items.append(f'{new_file.path} is linked to {new_path}')
        new_file = host.file(new_path)
        if not new_file.is_symlink:
            return new_file.path
        resolve_depth -= 1

    resolve_out = '\n'.join(resolve_items)
    raise ValueError(f'Broken or circular symlink found: {initial_file}\n'
                     f'Full resolution output:\n{resolve_out}')


def has_missing_shared_libraries(file_: GNUFile) -> MissingSOResult:
    """
    Checks if requested file has broken shared libraries dependencies

    Parameters
    ----------
    file_:  GNUFile
        File to test

    Returns
    -------
    MissingSOResult
        Result of the test

    """
    output = file_.run(f'ldd {file_.path}')
    result = []
    assert output.rc == 0
    for item in output.stdout.split('\n'):
        if 'not found' in item.lower():
            result.append(item)

    if len(result):
        return MissingSOResult(True, '\n'.join(result))
    return MissingSOResult(False, '')
