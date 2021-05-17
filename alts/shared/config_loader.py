import os
from typing import Type

from pydantic import BaseModel
from ruamel.yaml import YAML

from alts.shared.exceptions import ConfigNotFoundError


__all__ = ['get_config_dict_from_yaml']


def get_config_dict_from_yaml(file_path: str,
                              config_class: Type[BaseModel]) -> BaseModel:
    if not os.path.exists(file_path):
        raise ConfigNotFoundError(f'Cannot load file {file_path}')

    loader = YAML(typ='safe')
    with open(file_path, 'rt') as f:
        return config_class.parse_obj(loader.load(f))
