"""Webhook 先回 200、再於背景比價並以 reply token 回覆（工作票 03）。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import GENERIC_ERROR_MESSAGE, app, settings
from services.parser import ParsedItem
from tests.fakes import FakeParser, generate_signature, pipeline_with

SECRET = "test_secret_bg"
TOKEN = "test_token_bg"

client = TestClient(app)


def text_event(text, event_id, reply_token, user_id="U_bg_user", redelivery=False):
    return {
        "type": "message",
        "message": {"type": "text", "id": f"m_{event_id}", "text": text, "quoteToken": "q"},
        "timestamp": 1625641600000,
        "source": {"type": "user", "userId": user_id},
        "replyToken": reply_token,
        "mode": "active",
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": redelivery},
    }


def signed(events):
    body = json.dumps({"destination": "U1234567890", "events": events})
    return body, {"Content-Type": "application/json", "X-Line-Signature": generate_signature(SECRET, body)}


def post(events):
    body, headers = signed(events)
    return client.post("/api/webhook", content=body, headers=headers)


@pytest.fixture
def line_api():
    """假 LINE Messaging API；回傳可檢查 reply_message / show_loading_animation 的 AsyncMock。"""
    api = AsyncMock()
    with patch("main.AsyncApiClient"), \
         patch("main.AsyncMessagingApi", return_value=api), \
         patch.object(settings, "line_channel_secret", SECRET), \
         patch.object(settings, "line_channel_access_token", TOKEN):
        yield api


def replies(api):
    """[(reply_token, message), ...]，依回覆順序。"""
    return [(c.args[0].reply_token, c.args[0].messages[0]) for c in api.reply_message.call_args_list]


async def asgi_post(body, headers, sent):
    """直接以 ASGI 呼叫 app，把送出的訊息依序記錄在 sent。

    TestClient 會等背景工作跑完才回傳，無法觀察「比價尚未完成時已回 200」，故在此手動呼叫。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/webhook",
        "raw_path": b"/api/webhook",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body.encode(), "more_body": False}
        await asyncio.Event().wait()  # 永不斷線

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)


@pytest.mark.anyio
async def test_responds_200_before_comparison_finishes_then_replies_in_background(line_api):
    release = asyncio.Event()
    real_pipeline = pipeline_with(FakeParser(ParsedItem(keyword_zh="藍牙耳機", perfected_keyword="藍牙耳機")))

    async def slow_pipeline(**kwargs):
        await release.wait()
        return await real_pipeline(**kwargs)

    body, headers = signed([text_event("藍牙耳機", "ev_slow", "token_slow")])
    sent = []
    with patch("main.compare_prices", slow_pipeline):
        call = asyncio.create_task(asgi_post(body, headers, sent))
        for _ in range(200):  # 最多等 2 秒
            if any(m["type"] == "http.response.body" for m in sent):
                break
            await asyncio.sleep(0.01)

        starts = [m for m in sent if m["type"] == "http.response.start"]
        assert starts, "比價仍在進行時就應已回應 LINE"
        assert starts[0]["status"] == 200
        assert line_api.reply_message.await_count == 0, "比價尚未完成就不該回覆"

        release.set()
        await asyncio.wait_for(call, timeout=5)

    [(reply_token, msg)] = replies(line_api)
    assert reply_token == "token_slow"
    assert msg.type == "flex"


def test_loading_animation_is_shown_before_comparison_starts(line_api):
    order = []
    real_pipeline = pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))
    line_api.show_loading_animation.side_effect = lambda req: order.append(("loading", req.chat_id))

    async def recording_pipeline(**kwargs):
        order.append(("compare", kwargs.get("text")))
        return await real_pipeline(**kwargs)

    with patch("main.compare_prices", recording_pipeline):
        assert post([text_event("商品", "ev_anim", "token_anim", user_id="U_anim")]).status_code == 200

    assert order == [("loading", "U_anim"), ("compare", "商品")]


def test_redelivered_event_is_replied_only_once(line_api):
    compare_calls = []
    real_pipeline = pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))

    async def counting_pipeline(**kwargs):
        compare_calls.append(kwargs)
        return await real_pipeline(**kwargs)

    with patch("main.compare_prices", counting_pipeline):
        assert post([text_event("商品", "ev_dup", "token_dup")]).status_code == 200
        assert post([text_event("商品", "ev_dup", "token_dup", redelivery=True)]).status_code == 200

    assert len(compare_calls) == 1
    assert [token for token, _ in replies(line_api)] == ["token_dup"]


def test_distinct_events_in_one_request_are_each_handled(line_api):
    with patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))):
        post([
            text_event("商品一", "ev_a", "token_a"),
            text_event("商品二", "ev_b", "token_b"),
        ])

    assert sorted(token for token, _ in replies(line_api)) == ["token_a", "token_b"]


@pytest.mark.anyio
async def test_events_in_one_request_are_compared_concurrently(line_api):
    # 同批事件不互相排隊，避免後面事件的 reply token 等到過期
    started = []
    both_started = asyncio.Event()
    real_pipeline = pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))

    async def waiting_pipeline(**kwargs):
        started.append(kwargs["text"])
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return await real_pipeline(**kwargs)

    body, headers = signed([text_event("商品一", "ev_c1", "token_c1"), text_event("商品二", "ev_c2", "token_c2")])
    with patch("main.compare_prices", waiting_pipeline):
        await asyncio.wait_for(asgi_post(body, headers, []), timeout=5)

    assert sorted(token for token, _ in replies(line_api)) == ["token_c1", "token_c2"]
    assert all(msg.type == "flex" for _, msg in replies(line_api))


@pytest.mark.anyio
async def test_redelivery_arriving_while_first_delivery_is_still_comparing_is_skipped(line_api):
    release = asyncio.Event()
    compare_calls = []
    real_pipeline = pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))

    async def slow_pipeline(**kwargs):
        compare_calls.append(kwargs)
        await release.wait()
        return await real_pipeline(**kwargs)

    first_body, first_headers = signed([text_event("商品", "ev_race", "token_race")])
    again_body, again_headers = signed([text_event("商品", "ev_race", "token_race", redelivery=True)])
    first_sent, again_sent = [], []
    with patch("main.compare_prices", slow_pipeline):
        first = asyncio.create_task(asgi_post(first_body, first_headers, first_sent))
        while not compare_calls:
            await asyncio.sleep(0.01)

        await asyncio.wait_for(asgi_post(again_body, again_headers, again_sent), timeout=5)
        assert again_sent[0]["status"] == 200

        release.set()
        await asyncio.wait_for(first, timeout=5)

    assert len(compare_calls) == 1
    assert [token for token, _ in replies(line_api)] == ["token_race"]


def test_events_are_not_claimed_when_access_token_is_missing(line_api):
    # 設定缺漏時丟棄的事件，修好設定後 LINE 重送仍應被處理
    with patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))):
        with patch.object(settings, "line_channel_access_token", ""), \
             patch.dict("os.environ", {"LINE_CHANNEL_ACCESS_TOKEN": ""}):
            assert post([text_event("商品", "ev_token", "token_missing")]).status_code == 200
        assert post([text_event("商品", "ev_token", "token_missing", redelivery=True)]).status_code == 200

    assert [token for token, _ in replies(line_api)] == ["token_missing"]


def test_comparison_exception_replies_degraded_message(line_api):
    async def broken_pipeline(**kwargs):
        raise RuntimeError("boom")

    with patch("main.compare_prices", broken_pipeline):
        assert post([text_event("商品", "ev_boom", "token_boom")]).status_code == 200

    [(reply_token, msg)] = replies(line_api)
    assert reply_token == "token_boom"
    assert msg.text == GENERIC_ERROR_MESSAGE


def test_failure_in_one_event_does_not_stop_later_events(line_api):
    # 第一個事件（選單指令）回覆時 LINE API 一律失敗（含降級回覆）；第二個事件仍須被處理
    async def reply(req):
        if req.reply_token == "token_menu":
            raise RuntimeError("line down")

    line_api.reply_message.side_effect = reply

    with patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))):
        response = post([
            text_event("新手指南", "ev_menu", "token_menu"),
            text_event("商品", "ev_ok", "token_ok"),
        ])

    assert response.status_code == 200
    ok_replies = [msg for token, msg in replies(line_api) if token == "token_ok"]
    assert [msg.type for msg in ok_replies] == ["flex"]


def test_client_setup_failure_still_replies_degraded_message_with_fresh_client(line_api):
    # 第一次建立 LINE 連線失敗、重建成功：使用者仍收到降級訊息
    with patch("main.AsyncApiClient", side_effect=[RuntimeError("client init failed"), MagicMock()]), \
         patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))):
        assert post([text_event("商品", "ev_setup", "token_setup")]).status_code == 200

    [(reply_token, msg)] = replies(line_api)
    assert reply_token == "token_setup"
    assert msg.text == GENERIC_ERROR_MESSAGE


def test_client_teardown_failure_does_not_reply_twice(line_api):
    # 事件已回覆後才在關閉連線時出錯：不應再補送降級訊息
    client_ctx = MagicMock()
    client_ctx.__aexit__.side_effect = RuntimeError("close failed")
    with patch("main.AsyncApiClient", return_value=client_ctx), \
         patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))):
        assert post([text_event("商品", "ev_close", "token_close")]).status_code == 200

    assert [(token, msg.type) for token, msg in replies(line_api)] == [("token_close", "flex")]


def test_unexpected_error_outside_events_does_not_crash_service(line_api):
    with patch("main.AsyncApiClient", side_effect=RuntimeError("client init failed")):
        response = post([text_event("商品", "ev_init", "token_init")])

    assert response.status_code == 200
    assert client.get("/api/health").status_code == 200


def test_deadline_is_counted_from_when_the_webhook_was_received(line_api):
    """15 秒截止自收到訊息起算：比價流程拿到的是收到 webhook 的時間，而非開始比價的時間。"""
    import time

    received = []
    real_pipeline = pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))

    async def recording_pipeline(**kwargs):
        received.append((kwargs.get("received_at"), time.monotonic()))
        return await real_pipeline(**kwargs)

    before = time.monotonic()
    with patch("main.compare_prices", recording_pipeline):
        assert post([text_event("商品", "ev_deadline", "token_deadline")]).status_code == 200

    [(received_at, compare_started)] = received
    assert received_at is not None
    assert before <= received_at <= compare_started
