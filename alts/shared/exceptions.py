# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-05-01

"""AlmaLinux Test System common error classes."""

__all__ = [
    'ALTSBaseError', 'ConfigNotFoundError', 'DBUpdateError',
    'InstallPackageError', 'ProvisionError', 'PublishArtifactsError',
    'StartEnvironmentError', 'StopEnvironmentError',
    'TerraformInitializationError', 'WorkDirPreparationError',
    'PackageIntegrityTestsError', 'UninstallPackageError',
    'VMImageNotFound',
]


class ALTSBaseError(Exception):

    """AlmaLinux Test System base error."""

    pass


class DBUpdateError(ALTSBaseError):

    """Database update error."""

    pass


class ConfigNotFoundError(ALTSBaseError, FileNotFoundError):

    """Error when configuration file cannot be found."""

    pass


class WorkDirPreparationError(ALTSBaseError):

    """Error when working directory cannot be created."""

    pass


class TerraformInitializationError(ALTSBaseError):

    """Testing instance start error."""

    pass


class StartEnvironmentError(ALTSBaseError):

    """Starting Terraform environment error."""

    pass


class ProvisionError(ALTSBaseError):

    """Provisioning environment error."""

    pass


class InstallPackageError(ALTSBaseError):

    """Error occurred while installing a package."""

    pass


class UninstallPackageError(ALTSBaseError):

    """Error occurred while uninstalling a package."""

    pass


class PublishArtifactsError(ALTSBaseError):

    """Error occurred while uploading artifacts to artifacts storage."""

    pass


class StopEnvironmentError(ALTSBaseError):

    """Destroying Terraform environment error."""

    pass


class PackageIntegrityTestsError(ALTSBaseError):
    pass


class VMImageNotFound(ALTSBaseError):
    pass
