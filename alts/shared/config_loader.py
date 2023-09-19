import os
from pathlib import Path
from typing import Type

from pydantic import BaseModel
from yaml import safe_load

from alts.shared.exceptions import ConfigNotFoundError


__all__ = ['get_config_dict_from_yaml']


def get_config_dict_from_yaml(
        file_path: Path,
        config_class: Type[BaseModel],
) -> BaseModel:
    if not file_path.exists():
        raise ConfigNotFoundError(f'Cannot load file {file_path}')

    with file_path.open(mode='rt') as fd:
        return config_class.parse_obj(safe_load(fd))
