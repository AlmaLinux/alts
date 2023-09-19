import os

from pathlib import Path


__all__ = ['get_abspath']


def get_abspath(file_path: Path) -> Path:
    return Path(os.path.expandvars(file_path.home())).absolute()
