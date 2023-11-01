import os
import re
from collections import namedtuple
from typing import List, Union

from testinfra.host import Host
from testinfra.modules.file import GNUFile
from testinfra.modules.package import DebianPackage, RpmPackage


__all__ = [
    'get_package_files',
    'get_shared_libraries',
    'has_missing_shared_libraries',
    'is_package_empty',
    'is_debuginfo_package',
    'is_file_dynamically_linked',
    'is_rpath_correct',
    'resolve_symlink',
    'MissingSOResult',
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
        command = f'rpm -ql --noghost {pkg.name}'
    else:
        raise ValueError(f'Unknown package type: {type(pkg)}')

    output = pkg.run(command)
    assert output.rc == 0
    return [item for item in output.stdout.strip().split('\n')
            if item and 'contains no files' not in item]


def get_shared_libraries(pkg: Union[DebianPackage, RpmPackage]) -> List[str]:
    """
    Returns a list of shared libraries from the package.

    Parameters
    ----------
    pkg: DebianPackage | RpmPackage
        Package instance

    Returns
    -------
    list
        List of shared libraries

    """
    shared_libs = []

    for file_ in get_package_files(pkg):
        if '.so' in os.path.basename(file_):
            shared_libs.append(file_)

    return shared_libs


def is_package_empty(pkg: Union[DebianPackage, RpmPackage]) -> bool:
    """
    Checks if package contains no files.

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


def is_debuginfo_package(pkg: Union[DebianPackage, RpmPackage]) -> bool:
    """
    Checks if packages is containing debug information

    Parameters
    ----------
    pkg: DebianPackage | RpmPackage
        Package instance

    Returns
    -------
    bool
        True if package is debuginfo, False otherwise

    """
    return bool(re.search(r'debug(info|source)', pkg.name))


def is_file_dynamically_linked(file_: GNUFile) -> bool:
    """
    Returns if file is dynamically linked.

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
    Resolve symlink until actual file or fail in case if symlink is broken.

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
    message = f'Broken or circular symlink found: {initial_file}'
    if resolve_out:
        message += '\nFull resolution output:\n{resolve_out}'
    raise ValueError(message)


def has_missing_shared_libraries(file_: GNUFile) -> MissingSOResult:
    """
    Checks if requested file has broken shared libraries dependencies.

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
    result = [f'File {file_.path} has missing shared libraries dependencies:']
    assert output.rc == 0
    for item in output.stdout.split('\n'):
        if 'not found' in item.lower():
            result.append(item)

    if len(result):
        return MissingSOResult(True, '\n'.join(result))
    return MissingSOResult(False, '')


def is_rpath_correct(file_: GNUFile) -> bool:
    """
    Checks RPATH correctness.

    Parameters
    ----------
    file_: GNUFile
        File to check RPATH in

    Returns
    -------
    bool
        True if RPATH is correct, False otherwise

    """
    output = file_.run(f'objdump -x {file_.path}')
    assert output.rc == 0
    return not bool(re.search(r'(RPATH|RUNPATH).*:$', output.stdout))
