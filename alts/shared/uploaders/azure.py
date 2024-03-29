import logging
import os
import typing

from azure.core.exceptions import HttpResponseError
from azure.storage.blob import BlobServiceClient

from alts.shared.uploaders.base import (
    BaseUploader,
    BaseLogsUploader,
    UploadError,
)


__all__ = ['AzureBaseUploader', 'AzureLogsUploader']


class AzureBaseUploader(BaseUploader):
    argument_required_message = "'upload_dir' argument is required"

    def __init__(self, connection_string: str, container_name: str):
        self._connection_string = connection_string
        self._container_name = container_name
        self._blob_client = BlobServiceClient.from_connection_string(
            connection_string)
        self._container_client = self._blob_client.get_container_client(
            container=container_name)
        self._logger = logging.getLogger(__name__)

    def upload_single_file(
            self,
            file_path: str,
            azure_upload_dir: str,
    ) -> str:
        blob_name = os.path.join(azure_upload_dir, file_path)
        blob_client = self._blob_client.get_blob_client(
            container=self._container_name, blob=blob_name)
        try:
            with open(file_path, mode='rb') as fd:
                blob_client.upload_blob(fd)
            return blob_client.url
        except HttpResponseError as e:
            self._logger.error(
                'Cannot upload artifact %s to Azure: %s',
                file_path, e,
            )

    def upload(
            self,
            artifacts_dir: str,
            upload_dir: str,
            **kwargs,
    ) -> typing.Tuple[typing.Dict[str, str], bool]:
        """
        Uploads files from provided directory into Azure Blob storage.

        Parameters
        ----------
        artifacts_dir : str
            Directory where local files are stored
        upload_dir: str
            Path to upload directory
        kwargs

        Returns
        -------
        list
            List of references to uploaded artifacts

        """
        # To avoid warning about signature we assume that `s3_upload_dir`
        # is required keyword argument.
        artifacts = {}
        success = True

        if not kwargs.get('upload_dir'):
            self._logger.error(self.argument_required_message)
            raise UploadError(self.argument_required_message)
        for file_ in self.get_artifacts_list(artifacts_dir):
            reference = self.upload_single_file(file_, upload_dir)
            if reference:
                artifacts[os.path.basename(file_)] = reference
            else:
                success = False
        return artifacts, success


class AzureLogsUploader(BaseLogsUploader, AzureBaseUploader):
    pass
