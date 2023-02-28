import pytest_check as check
from testinfra.modules.package import RpmPackage

from ..base import *


def test_package_is_installed(host, package_name, package_version):
    """
    Test that package is installed on the system.

    Parameters
    ----------
    host:               Host
        Host reference
    package_name:       str
        Package name
    package_version:    str
        Package version

    Returns
    -------

    """

    package = host.package(package_name)
    check.is_true(package.is_installed)
    if package_version:
        check.is_in(
            package.version, package_version,
            'Version does not match: required: {0}, actual: {1}'.format(
                package_version, package.version)
        )
        if isinstance(package, RpmPackage):
            check.is_in(
                package.release, package_version,
                'Release does not match: required: {0}, actual: {1}'.format(
                    package_version, package.release)
            )


def test_all_package_files_exist(host, package_name):
    """
    Check that all files from the package are present on the filesystem.

    Parameters
    ----------
    host:               Host
        Host reference
    package_name:       str
        Package name

    Returns
    -------

    """

    package = host.package(package_name)
    if is_package_empty(package):
        return
    if is_debuginfo_package(package):
        return

    for file_ in get_package_files(package):
        file_obj = host.file(file_)
        check.is_true(
            file_obj.exists,
            'File is absent on the file system: {}'.format(file_)
        )
        # Check all symlinks point to existing files
        if file_obj.is_symlink:
            resolved = resolve_symlink(host, file_obj)
            check.is_not_none(
                resolved, 'Symlink cannot be resolved: {}'.format(file_))
            file_obj = host.file(resolved)
            check.is_true(
                file_obj.exists, 'Symlink is broken: {}'.format(file_))


def test_binaries_have_all_dependencies(host, package_name):
    """
    Check that all binaries have corresponding shared libraries.

    Parameters
    ----------
    host:               Host
        Host reference
    package_name:       str
        Package name

    Returns
    -------

    """

    package = host.package(package_name)
    if is_package_empty(package):
        return
    if is_debuginfo_package(package):
        return

    for file_path in get_package_files(package):
        file_ = host.file(file_path)
        if file_.is_symlink:
            file_ = host.file(resolve_symlink(host, file_))
        if is_file_dynamically_linked(file_):
            check_result = has_missing_shared_libraries(file_)
            check.is_false(check_result.missing, check_result.output)


def test_check_rpath_is_correct(host, package_name):
    """
    Check that RPATH does not have errors in definition
    (for example, ':' in the end).

    Parameters
    ----------
    host:           Host
        Host reference
    package_name:   str
        Package name

    Returns
    -------

    """
    package = host.package(package_name)
    if is_package_empty(package):
        return
    if is_debuginfo_package(package):
        return

    for library in get_shared_libraries(package):
        check.is_true(is_rpath_correct(host.file(library)),
                      'RPATH is broken in {}'.format(library))
