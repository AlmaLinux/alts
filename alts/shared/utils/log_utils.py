import os
from tempfile import NamedTemporaryFile
from typing import IO, Tuple


def get_temp_log_files(prefix: str) -> Tuple[IO, IO]:
    temp_file_kwargs = {
        'delete': False,
        'mode': 'w+',
        'prefix': prefix,
    }
    return (
        NamedTemporaryFile(**temp_file_kwargs, suffix='.stdout.log'),
        NamedTemporaryFile(**temp_file_kwargs, suffix='.stderr.log'),
    )


def read_and_cleanup_temp_log_files(
    out_file: IO[str],
    err_file: IO[str],
) -> Tuple[str, str]:
    for file in (out_file, err_file):
        file.seek(0)
    out = f'\n{out_file.read()}'
    err = f'\n{err_file.read()}'
    for file in (out_file, err_file):
        file.close()
        os.unlink(file.name)
    return out, err
