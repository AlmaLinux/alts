"""
Configuration file for tests
"""
import pytest


def pytest_addoption(parser):
    """
    Add package-name and package-version command line parameters
    to pass from py.test

    Parameters
    ----------
    parser

    Returns
    -------

    """
    parser.addoption('--package-name', action='store', required=True)
    parser.addoption('--package-version', action='store', required=False,
                     default='')


@pytest.fixture()
def package_name(pytestconfig):
    return pytestconfig.getoption('package_name')


@pytest.fixture()
def package_version(pytestconfig):
    return pytestconfig.getoption('package_version')
