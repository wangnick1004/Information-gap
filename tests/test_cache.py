import time
from services.cache import TTLCache


def test_ttl_cache_basic_get_set():
    cache = TTLCache(default_ttl=10.0, max_size=5)
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"
    assert "key1" in cache
    assert len(cache) == 1


def test_ttl_cache_expiration():
    # Cache with very short TTL
    cache = TTLCache(default_ttl=0.1, max_size=5)
    cache.set("short_lived", {"data": 123}, ttl=0.05)
    assert cache.get("short_lived") == {"data": 123}

    time.sleep(0.06)
    assert cache.get("short_lived") is None
    assert "short_lived" not in cache


def test_ttl_cache_max_size_eviction():
    cache = TTLCache(default_ttl=60.0, max_size=3)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")
    assert len(cache) == 3

    # Adding 4th item should evict oldest
    cache.set("k4", "v4")
    assert len(cache) <= 3
    assert cache.get("k4") == "v4"


def test_ttl_cache_delete_and_clear():
    cache = TTLCache(default_ttl=60.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    assert len(cache) == 2

    cache.delete("k1")
    assert cache.get("k1") is None
    assert cache.get("k2") == "v2"

    cache.clear()
    assert len(cache) == 0
    assert cache.get("k2") is None
