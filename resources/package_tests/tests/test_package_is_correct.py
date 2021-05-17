from testinfra.modules.package import RpmPackage

from ..base import *


def test_package_is_installed(host, package_name, package_version):
    """
    Test that package is installed on the system

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
    assert package.is_installed
    if package_version:
        assert package.version in package_version
        if isinstance(package, RpmPackage):
            assert package.release in package_version


def test_all_package_files_exist(host, package_name):
    """
    Check that all files from the package are present on the filesystem

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

    for file_ in get_package_files(package):
        assert host.file(file_).exists


def test_binaries_have_all_dependencies(host, package_name):
    """
    Check that all binaries have corresponding shared libraries

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

    for file_path in get_package_files(package):
        file_ = host.file(file_path)
        if file_.is_symlink:
            file_ = host.file(resolve_symlink(host, file_))
        if is_file_dynamically_linked(file_):
            check_result = has_missing_shared_libraries(file_)
            assert not check_result.missing, check_result.output
