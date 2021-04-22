
__all__ = ['BaseError', 'InstallPackageError', 'ProvisionError',
           'PublishArtifactsError', 'StartEnvironmentError',
           'StopEnvironmentError', 'TerraformInitializationError',
           'WorkDirPreparationError']


class BaseError(Exception):
    pass


class WorkDirPreparationError(BaseError):
    pass


class TerraformInitializationError(BaseError):
    pass


class StartEnvironmentError(BaseError):
    pass


class ProvisionError(BaseError):
    pass


class InstallPackageError(BaseError):
    pass


class PublishArtifactsError(BaseError):
    pass


class StopEnvironmentError(BaseError):
    pass
