__all__ = ['ALTSBaseError', 'ConfigNotFoundError', 'DBUpdateError',
           'InstallPackageError', 'ProvisionError', 'PublishArtifactsError',
           'StartEnvironmentError', 'StopEnvironmentError',
           'TerraformInitializationError', 'WorkDirPreparationError']


class ALTSBaseError(Exception):
    pass


class DBUpdateError(ALTSBaseError):
    pass


class ConfigNotFoundError(ALTSBaseError, FileNotFoundError):
    pass


class WorkDirPreparationError(ALTSBaseError):
    pass


class TerraformInitializationError(ALTSBaseError):
    pass


class StartEnvironmentError(ALTSBaseError):
    pass


class ProvisionError(ALTSBaseError):
    pass


class InstallPackageError(ALTSBaseError):
    pass


class PublishArtifactsError(ALTSBaseError):
    pass


class StopEnvironmentError(ALTSBaseError):
    pass
