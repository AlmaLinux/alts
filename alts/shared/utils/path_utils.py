import os


__all__ = ['get_abspath']


def get_abspath(file_path: str) -> str:
    return os.path.abspath(
        os.path.expandvars(
            os.path.expanduser(file_path)
        )
    )
