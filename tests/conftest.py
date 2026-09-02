import pytest
from services.cache import search_cache
from main import user_request_timestamps


@pytest.fixture(autouse=True)
def clear_cache_before_each_test():
    """Ensure in-memory search cache and rate limiter state are cleared between tests for complete test isolation."""
    search_cache.clear()
    user_request_timestamps.clear()
    yield
    search_cache.clear()
    user_request_timestamps.clear()
