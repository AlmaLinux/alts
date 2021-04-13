
class BaseError(Exception):
    pass


class WorkDirPreparationError(BaseError):
    pass


class TerraformInitializationError(BaseError):
    pass


class EnvironmentStartError(BaseError):
    pass


class ProvisionError(BaseError):
    pass


class InstallPackageError(BaseError):
    pass


class DestroyEnvironmentError(BaseError):
    pass
