"""評測程式（evaluation.harness）的測試：經由真實的比價流程入口，只換 AI、平台轉接器與時鐘。"""

import asyncio
import csv
import dataclasses
import functools
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evaluation.harness import (
    EvaluationSetError,
    load_cases,
    read_records_csv,
    run_evaluation,
    summarize,
    write_records_csv,
)
from services.categories import Category
from services.comparison import compare_prices
from services.parser import GeminiServerError, IrrelevantPostError, ParsedItem
from services.platforms import FetchStatus
from tests.fakes import FakeClock, FakeParser, SlowAdapter, failed, fake_platforms, found

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def write_set(tmp_path, body):
    path = tmp_path / "set.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def xm5_item(**overrides):
    fields = dict(
        keyword_zh="Sony WH-1000XM5 耳機",
        keyword_jp="ソニー WH-1000XM5",
        perfected_keyword="Sony WH-1000XM5 耳機",
        search_query_ja="ソニー WH-1000XM5",
        category="動漫周邊/玩具",
    )
    fields.update(overrides)
    return ParsedItem(**fields)


TEXT_CASE = """
    - id: xm5
      input:
        text: 收 sony xm5 耳機 預算 8000
      category: 3C 家電
      expected:
        name: Sony WH-1000XM5 無線降噪耳機
        model: WH-1000XM5
        urls:
          - https://www.sony.com.tw/zh/electronics/headband-headphones/wh-1000xm5
"""


def pipeline(parser, clock, **adapters):
    """真實的比價流程入口，換上假 AI 與假平台；時鐘同評測程式。"""
    return functools.partial(compare_prices, parser=parser, platforms=fake_platforms(**adapters), clock=clock)


def evaluate(cases, parser, clock=None, **adapters):
    clock = clock or FakeClock(FIXED_NOW)
    return asyncio.run(run_evaluation(cases, compare=pipeline(parser, clock, **adapters), clock=clock))


# ---- 評測集格式 ----

def test_loads_text_and_image_cases(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "bag.jpg").write_bytes(b"jpeg bytes")
    path = write_set(tmp_path, TEXT_CASE + """
    - id: bag
      input:
        image: images/bag.jpg
      category: 服飾鞋包
      expected:
        name: Longchamp Le Pliage 中型托特包
        model: Le Pliage 1899
        match_terms: [Le Pliage]
        urls: [https://www.longchamp.com/tw/zh/products/le-pliage-original/l1899089.html]
      source:
        platform: Dcard
        date: 2026-09-20
    """)

    text_case, image_case = load_cases(path)

    assert text_case.id == "xm5"
    assert text_case.input_type == "text"
    assert text_case.text == "收 sony xm5 耳機 預算 8000"
    assert text_case.category is Category.ELECTRONICS
    assert text_case.match_terms == ("WH-1000XM5",)  # 未指定時以型號規格比對
    assert image_case.input_type == "image"
    assert image_case.image == b"jpeg bytes"
    assert image_case.match_terms == ("Le Pliage",)
    assert image_case.source == "Dcard 2026-09-20"


@pytest.mark.parametrize("broken, message", [
    ("category: 3C 家電", "input"),
    ("input: {text: a, image: b.jpg}\n      category: 3C 家電", "input"),
    ("input: {text: a}\n      category: 家具", "category"),
    ("input: {text: a}\n      category: 3C 家電\n      expected: {name: A, model: B, urls: []}", "urls"),
    ("input: {text: a}\n      category: 3C 家電\n      expected: {name: A, urls: [https://a.tw]}", "model"),
    ("input: {image: missing.jpg}\n      category: 3C 家電\n      expected: {name: A, model: B, urls: [https://a.tw]}",
     "missing.jpg"),
])
def test_rejects_incomplete_cases_with_the_case_id(tmp_path, broken, message):
    path = write_set(tmp_path, f"""
    - id: broken-one
      {broken}
    """)

    with pytest.raises(EvaluationSetError, match=message) as error:
        load_cases(path)
    assert "broken-one" in str(error.value)


def test_rejects_duplicate_ids(tmp_path):
    path = write_set(tmp_path, TEXT_CASE + TEXT_CASE)

    with pytest.raises(EvaluationSetError, match="xm5"):
        load_cases(path)


def test_bundled_sample_set_loads():
    cases = load_cases(PROJECT_ROOT / "evaluation" / "sample_set.yaml")

    assert 3 <= len(cases) <= 5
    assert all(case.urls for case in cases)


# ---- 逐筆執行（經由比價流程入口） ----

def test_records_timing_platform_statuses_and_match_for_a_text_case(tmp_path):
    cases = load_cases(write_set(tmp_path, TEXT_CASE))
    parser = FakeParser(xm5_item())

    [record] = evaluate(cases, parser, mercari=found(30000), rakuten=failed(FetchStatus.BLOCKED))

    assert parser.calls[0]["post_text"] == "收 sony xm5 耳機 預算 8000"
    assert record.case_id == "xm5"
    assert record.within_deadline
    assert record.price_platforms == 1
    assert record.platform_statuses["mercari"] == "ok"
    assert record.platform_statuses["rakuten"] == "failed"
    assert record.ai_category == "動漫周邊/玩具"
    assert record.keyword_zh == "Sony WH-1000XM5 耳機"
    assert record.auto_correct is True


def test_image_cases_send_the_image_bytes(tmp_path):
    (tmp_path / "xm5.jpg").write_bytes(b"photo")
    cases = load_cases(write_set(tmp_path, """
    - id: photo
      input: {image: xm5.jpg}
      category: 3C 家電
      expected: {name: Sony WH-1000XM5, model: WH-1000XM5, urls: [https://a.tw]}
    """))
    parser = FakeParser(xm5_item())

    [record] = evaluate(cases, parser)

    assert parser.calls[0]["image_data"] == b"photo"
    assert record.input_type == "image"
    assert record.auto_correct is True


def test_measures_elapsed_time_on_the_given_clock(tmp_path):
    clock = FakeClock(FIXED_NOW)
    cases = load_cases(write_set(tmp_path, TEXT_CASE))

    [record] = evaluate(cases, FakeParser(xm5_item()), clock, mercari=SlowAdapter(clock, 4.0, found(30000)))

    assert record.elapsed_seconds == pytest.approx(4.0)


def test_replies_slower_than_15_seconds_are_not_within_deadline(tmp_path):
    clock = FakeClock(FIXED_NOW)
    cases = load_cases(write_set(tmp_path, TEXT_CASE))

    async def slow_compare(**kwargs):
        await clock.sleep(16)
        return await pipeline(FakeParser(xm5_item()), clock)(**kwargs)

    [record] = asyncio.run(run_evaluation(cases, compare=slow_compare, clock=clock))

    assert not record.within_deadline


@pytest.mark.parametrize("keyword", ["Sony WH1000XM5", "sony wh-1000 xm5", "ＷＨ－１０００ＸＭ５"])
def test_match_ignores_case_spacing_and_punctuation(tmp_path, keyword):
    cases = load_cases(write_set(tmp_path, TEXT_CASE))

    [record] = evaluate(cases, FakeParser(xm5_item(perfected_keyword=keyword, keyword_zh=keyword)))

    assert record.auto_correct is True


def test_a_different_model_is_not_a_match(tmp_path):
    cases = load_cases(write_set(tmp_path, TEXT_CASE))
    parser = FakeParser(xm5_item(perfected_keyword="Sony WH-1000XM4", keyword_zh="Sony WH-1000XM4",
                                 search_query_ja="ソニー WH-1000XM4", keyword_jp="ソニー WH-1000XM4"))

    [record] = evaluate(cases, parser)

    assert record.auto_correct is False


@pytest.mark.parametrize("term, keyword", [
    ("Switch", "Nintendo Switch 2"),        # 不同世代
    ("230", "SK-II 青春露 2300ml"),    # 不同容量
    ("WH-1000XM5", "Sony WH-1000XM50"),
])
def test_a_term_does_not_match_a_longer_number_in_the_keyword(tmp_path, term, keyword):
    cases = load_cases(write_set(tmp_path, TEXT_CASE.replace("model: WH-1000XM5", f"model: {term}")))
    parser = FakeParser(xm5_item(perfected_keyword=keyword, keyword_zh=keyword, search_query_ja=keyword, keyword_jp=keyword))

    [record] = evaluate(cases, parser)

    assert record.auto_correct is False


@pytest.mark.parametrize("term, keyword", [
    ("青春露", "SK-II 青春露 230ml"),   # 中文品名後接容量：拿掉空白後緊接數字，仍算吻合
    ("小棕瓶", "Estée Lauder 小棕瓶 50ml"),
])
def test_a_chinese_term_followed_by_a_size_is_a_match(tmp_path, term, keyword):
    cases = load_cases(write_set(tmp_path, TEXT_CASE.replace("model: WH-1000XM5", f"model: {term}")))
    parser = FakeParser(xm5_item(perfected_keyword=keyword, keyword_zh=keyword, search_query_ja=keyword, keyword_jp=keyword))

    [record] = evaluate(cases, parser)

    assert record.auto_correct is True


@pytest.mark.parametrize("error", [GeminiServerError("down"), IrrelevantPostError("not shopping")])
def test_ai_failure_or_irrelevant_is_not_a_match_even_if_the_raw_text_names_the_model(tmp_path, error):
    cases = load_cases(write_set(tmp_path, TEXT_CASE.replace("收 sony xm5", "收 WH-1000XM5")))

    [record] = evaluate(cases, FakeParser(error=error))

    assert record.auto_correct is False
    assert record.price_platforms == 0


def test_an_image_case_whose_ai_fails_is_recorded_as_an_error_and_the_run_continues(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"photo")
    cases = load_cases(write_set(tmp_path, """
    - id: photo
      input: {image: a.jpg}
      category: 3C 家電
      expected: {name: A, model: WH-1000XM5, urls: [https://a.tw]}
    """ + TEXT_CASE))

    photo, text = evaluate(cases, FakeParser(error=GeminiServerError("down")))

    assert photo.error == "GeminiServerError"
    assert photo.auto_correct is False
    assert text.error == ""


def test_each_case_runs_the_full_flow_without_reusing_cached_results(tmp_path):
    cases = load_cases(write_set(tmp_path, TEXT_CASE + TEXT_CASE.replace("id: xm5", "id: xm5-again")))
    parser = FakeParser(xm5_item())

    first, second = evaluate(cases, parser, mercari=found(30000))

    assert len(parser.calls) == 2
    assert not second.from_cache


# ---- 指標 ----

def record_with(tmp_path, case_id, category, input_type, *, elapsed, prices, correct, manual=None):
    """以真實流程產生一筆紀錄後改寫要比較的欄位，方便組出指定的指標。"""
    return dataclasses.replace(
        make_record(tmp_path),
        case_id=case_id, category=category, input_type=input_type,
        elapsed_seconds=elapsed, price_platforms=prices, auto_correct=correct, manual_correct=manual,
    )


def make_record(tmp_path):
    [record] = evaluate(load_cases(write_set(tmp_path, TEXT_CASE)), FakeParser(xm5_item()))
    return record


def test_summary_reports_three_metrics_overall_and_by_group(tmp_path):
    records = [
        record_with(tmp_path, "a", "3C 家電", "text", elapsed=3.0, prices=4, correct=True),
        record_with(tmp_path, "b", "3C 家電", "image", elapsed=16.0, prices=0, correct=False),
        record_with(tmp_path, "c", "美妝保養", "text", elapsed=5.0, prices=2, correct=True),
        record_with(tmp_path, "d", "美妝保養", "text", elapsed=15.0, prices=6, correct=False),
    ]

    summary = summarize(records)

    overall = summary["overall"]["全部"]
    assert overall.cases == 4
    assert overall.within_deadline_rate == pytest.approx(0.75)
    assert overall.avg_price_platforms == pytest.approx(3.0)
    assert overall.accuracy == pytest.approx(0.5)
    assert summary["category"]["3C 家電"].within_deadline_rate == pytest.approx(0.5)
    assert summary["category"]["美妝保養"].avg_price_platforms == pytest.approx(4.0)
    assert summary["input_type"]["text"].cases == 3
    assert summary["input_type"]["image"].accuracy == pytest.approx(0.0)


def test_manual_review_overrides_the_automatic_match(tmp_path):
    records = [
        record_with(tmp_path, "a", "3C 家電", "text", elapsed=3.0, prices=4, correct=False, manual=True),
        record_with(tmp_path, "b", "3C 家電", "text", elapsed=3.0, prices=4, correct=True, manual=False),
        record_with(tmp_path, "c", "3C 家電", "text", elapsed=3.0, prices=4, correct=True),
    ]

    assert summarize(records)["overall"]["全部"].accuracy == pytest.approx(2 / 3)


# ---- CSV 明細 ----

def test_csv_round_trip_keeps_fields_and_reads_manual_review(tmp_path):
    [record] = evaluate(load_cases(write_set(tmp_path, TEXT_CASE)), FakeParser(xm5_item()), mercari=found(30000))
    out = tmp_path / "records.csv"
    write_records_csv([record], out)

    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "manual_correct" in header and "auto_correct" in header and "mercari" in header

    # 研究者在試算表裡填上人工覆核結果
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["manual_correct"] = "否"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    [loaded] = read_records_csv(out)

    assert loaded.case_id == "xm5"
    assert loaded.elapsed_seconds == pytest.approx(record.elapsed_seconds)
    assert loaded.price_platforms == 1
    assert loaded.platform_statuses == record.platform_statuses
    assert loaded.auto_correct is True
    assert loaded.manual_correct is False
    assert summarize([loaded])["overall"]["全部"].accuracy == 0.0
