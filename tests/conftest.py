import pytest
from services.cache import search_cache
from main import processed_webhook_event_ids, user_request_timestamps


@pytest.fixture(autouse=True)
def clear_cache_before_each_test():
    """Ensure in-memory search cache and rate limiter / webhook dedupe state are cleared between tests for complete test isolation."""
    search_cache.clear()
    user_request_timestamps.clear()
    processed_webhook_event_ids.clear()
    yield
    search_cache.clear()
    user_request_timestamps.clear()
    processed_webhook_event_ids.clear()
