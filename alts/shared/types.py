# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2021-04-22

"""AlmaLinux Test System data types."""

__all__ = ['ImmutableDict']


# Immutable dict implementation. See https://www.python.org/dev/peps/pep-0351/
class ImmutableDict(dict):

    """Immutable dictionary implementation."""

    def __hash__(self):
        return id(self)

    def _immutable(self, *args, **kws):
        raise TypeError('object is immutable')

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    update = _immutable
    setdefault = _immutable
    pop = _immutable
    popitem = _immutable