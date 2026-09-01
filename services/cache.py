import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("line_bot.cache")


class TTLCache:
    """
    In-memory Time-To-Live (TTL) cache for search results and parsed items.
    
    Stores entries with expiration timestamps.
    Automatically expires entries after the configured TTL (default: 3600 seconds / 1 hour).
    """

    def __init__(self, default_ttl: float = 3600.0, max_size: int = 1000) -> None:
        self.default_ttl = float(default_ttl)
        self.max_size = int(max_size)
        # Store key -> (value, expire_timestamp)
        self._cache: Dict[str, Tuple[Any, float]] = {}

    def _cleanup(self) -> None:
        """Purge expired entries from the cache."""
        now = time.time()
        expired_keys = [k for k, (_, exp) in self._cache.items() if now >= exp]
        for k in expired_keys:
            del self._cache[k]

        # Evict oldest items if exceeding max_size
        if len(self._cache) > self.max_size:
            excess = len(self._cache) - self.max_size
            keys_to_remove = list(self._cache.keys())[:excess]
            for k in keys_to_remove:
                self._cache.pop(k, None)

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a value from the cache if it exists and has not expired.
        
        Args:
            key: Cache lookup key.
            
        Returns:
            Cached value if valid, None otherwise.
        """
        if not key:
            return None

        entry = self._cache.get(key)
        if entry is None:
            return None

        value, expire_at = entry
        if time.time() >= expire_at:
            # Expired
            self._cache.pop(key, None)
            logger.debug(f"Cache expired for key: '{key}'")
            return None

        logger.debug(f"Cache hit for key: '{key}'")
        return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Store a value in the cache with a TTL.
        
        Args:
            key: Cache lookup key.
            value: Object to store.
            ttl: Time-to-live in seconds (defaults to self.default_ttl).
        """
        if not key:
            return

        self._cleanup()
        ttl_seconds = self.default_ttl if ttl is None else float(ttl)
        expire_at = time.time() + ttl_seconds
        self._cache[key] = (value, expire_at)
        logger.debug(f"Cached key '{key}' with TTL {ttl_seconds}s (expires in {ttl_seconds/60:.1f} mins)")

    def delete(self, key: str) -> None:
        """Remove a specific key from the cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all entries from the cache."""
        self._cache.clear()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        self._cleanup()
        return len(self._cache)


# Global singleton cache instance for search and parser results (1 hour TTL)
search_cache = TTLCache(default_ttl=3600.0, max_size=1000)
