import pytest
from services.cache import search_cache


@pytest.fixture(autouse=True)
def clear_cache_before_each_test():
    """Ensure in-memory search cache is cleared between tests for complete test isolation."""
    search_cache.clear()
    yield
    search_cache.clear()
