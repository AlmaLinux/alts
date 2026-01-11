# -*- mode:python; coding:utf-8; -*-

"""OpenNebula quota cache for Celery worker processes."""

import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

__all__ = ['OpenNebulaQuotaCache', 'QuotaInfo']


@dataclass
class QuotaInfo:
    """OpenNebula quota information."""
    vms_used: int
    vms_limit: int
    cpu_used: float
    cpu_limit: float
    memory_used: int
    memory_limit: int
    disk_used: int
    disk_limit: int
    timestamp: float


class OpenNebulaQuotaCache:
    """
    Singleton cache for OpenNebula quota information.

    This cache is per-worker process and uses TTL-based expiration.
    Thread-safe for concurrent task access within a worker.
    """
    _instance: Optional['OpenNebulaQuotaCache'] = None
    _lock = Lock()

    def __new__(cls, ttl: int = 30):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._cache = {}
                cls._instance._ttl = ttl
                cls._instance._logger = logging.getLogger(__name__)
        return cls._instance

    def get(self, group_id: int) -> Optional[QuotaInfo]:
        """
        Get cached quota info for a group if not expired.

        Parameters
        ----------
        group_id : int
            OpenNebula group ID.

        Returns
        -------
        Optional[QuotaInfo]
            Cached quota info or None if not cached or expired.
        """
        with self._lock:
            if group_id not in self._cache:
                return None
            quota = self._cache[group_id]
            if self._is_expired(quota):
                self._logger.debug(
                    'Quota cache expired for group %d', group_id
                )
                del self._cache[group_id]
                return None
            return quota

    def set(self, group_id: int, quota: QuotaInfo):
        """
        Store quota info in cache with current timestamp.

        Parameters
        ----------
        group_id : int
            OpenNebula group ID.
        quota : QuotaInfo
            Quota information to cache.
        """
        with self._lock:
            quota.timestamp = time.time()
            self._cache[group_id] = quota
            self._logger.debug(
                'Cached quota for group %d: VMs %d/%d, CPU %.1f/%.1f, '
                'Memory %d/%d MB, Disk %d/%d MB',
                group_id,
                quota.vms_used, quota.vms_limit,
                quota.cpu_used, quota.cpu_limit,
                quota.memory_used, quota.memory_limit,
                quota.disk_used, quota.disk_limit
            )

    def _is_expired(self, quota: QuotaInfo) -> bool:
        """Check if cached quota entry has expired."""
        return time.time() - quota.timestamp > self._ttl

    def invalidate(self, group_id: int):
        """
        Remove cached quota for a specific group.

        Parameters
        ----------
        group_id : int
            OpenNebula group ID.
        """
        with self._lock:
            if group_id in self._cache:
                del self._cache[group_id]
                self._logger.debug(
                    'Invalidated quota cache for group %d', group_id
                )

    def clear(self):
        """Clear all cached quota entries."""
        with self._lock:
            self._cache.clear()
            self._logger.debug('Cleared all quota cache entries')

    def set_ttl(self, ttl: int):
        """
        Update the cache TTL.

        Parameters
        ----------
        ttl : int
            New TTL in seconds.
        """
        with self._lock:
            self._ttl = ttl
