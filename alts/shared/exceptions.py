# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System common error classes."""

__all__ = [
    'ALTSBaseError',
    'ConfigNotFoundError',
    'DBUpdateError',
    'InstallPackageError',
    'ProvisionError',
    'PublishArtifactsError',
    'StartEnvironmentError',
    'StopEnvironmentError',
    'SystemInfoCmdError',
    'TerraformInitializationError',
    'ThirdPartyTestError',
    'WorkDirPreparationError',
    'PackageIntegrityTestsError',
    'UninstallPackageError',
    'VMImageNotFound',
]


class ALTSBaseError(Exception):
    """AlmaLinux Test System base error."""


class DBUpdateError(ALTSBaseError):
    """Database update error."""


class ConfigNotFoundError(ALTSBaseError, FileNotFoundError):
    """Error when configuration file cannot be found."""


class WorkDirPreparationError(ALTSBaseError):
    """Error when working directory cannot be created."""


class TerraformInitializationError(ALTSBaseError):
    """Testing instance start error."""


class StartEnvironmentError(ALTSBaseError):
    """Starting Terraform environment error."""


class ProvisionError(ALTSBaseError):
    """Provisioning environment error."""


class InstallPackageError(ALTSBaseError):
    """Error occurred while installing a package."""


class UninstallPackageError(ALTSBaseError):
    """Error occurred while uninstalling a package."""


class PublishArtifactsError(ALTSBaseError):
    """Error occurred while uploading artifacts to artifacts storage."""


class StopEnvironmentError(ALTSBaseError):
    """Destroying Terraform environment error."""


class PackageIntegrityTestsError(ALTSBaseError):
    pass


class VMImageNotFound(StartEnvironmentError):
    pass


class ThirdPartyTestError(ALTSBaseError):
    """Error occurred while running third party test"""


class SystemInfoCmdError(ALTSBaseError):
    pass
