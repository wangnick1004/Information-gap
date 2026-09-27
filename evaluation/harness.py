"""
評測程式：讀取評測集，逐筆透過比價流程入口（services.comparison.compare_prices，與正式服務同一入口）執行，
記錄耗時、各平台狀態與相符判斷，輸出三項指標（15 秒內回覆比例、平均取得真實價格的平台數、比對正確率），
並依類別與輸入型態（文字／圖片）分組。評測集格式與判定方式見 evaluation/README.md。

用法：
    python -m evaluation.harness run evaluation/sample_set.yaml --out evaluation/results/
    python -m evaluation.harness run SET.yaml --out DIR --evaluation-mode   # 啟用付費 API 對照
    python -m evaluation.harness summarize DIR/records.csv                  # 人工覆核後重算指標
"""

import argparse
import asyncio
import csv
import dataclasses
import functools
import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

from services.cache import TTLCache
from services.categories import CATEGORY_PLATFORMS, Category
from services.clock import Clock, system_clock
from services.comparison import DEADLINE_SECONDS, ComparisonResult, PlatformStatus, compare_prices
from services.platforms import build_platforms

logger = logging.getLogger("line_bot.evaluation")

TEXT = "text"
IMAGE = "image"

Compare = Callable[..., Awaitable[ComparisonResult]]

# 候選平台池（CSV 每個平台固定兩欄：狀態與相符最低價）
PLATFORM_POOL: Tuple[str, ...] = tuple(dict.fromkeys(name for names in CATEGORY_PLATFORMS.values() for name in names))

_TRUE_WORDS = {"1", "是", "true"}
_FALSE_WORDS = {"0", "否", "false"}


class EvaluationSetError(ValueError):
    """評測集格式錯誤（訊息含出錯的那筆 id）。"""


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    input_type: str
    category: Category
    name: str
    model: str
    match_terms: Tuple[str, ...]
    urls: Tuple[str, ...]
    text: Optional[str] = None
    image: Optional[bytes] = None
    # 圖片檔相對於評測集檔的路徑；文字輸入為空
    image_path: str = ""
    source: str = ""


@dataclass(frozen=True)
class EvaluationRecord:
    case_id: str
    input_type: str
    # 人工標註的類別（分組依據）；AI 判斷的類別另記於 ai_category
    category: str
    query: str
    expected_name: str
    expected_model: str
    match_terms: Tuple[str, ...]
    expected_urls: Tuple[str, ...]
    source: str
    elapsed_seconds: float
    ai_category: str = ""
    keyword_zh: str = ""
    keyword_jp: str = ""
    price_platforms: int = 0
    platform_statuses: Dict[str, str] = field(default_factory=dict)
    platform_prices: Dict[str, Optional[int]] = field(default_factory=dict)
    ai_unavailable: bool = False
    from_cache: bool = False
    # 比價流程拋出的例外類別名稱（例如圖片輸入遇到 AI 故障）；正常為空字串
    error: str = ""
    auto_correct: bool = False
    # 人工覆核結果；None 表示未覆核，採自動判定
    manual_correct: Optional[bool] = None

    @property
    def within_deadline(self) -> bool:
        return self.elapsed_seconds <= DEADLINE_SECONDS

    @property
    def correct(self) -> bool:
        return self.auto_correct if self.manual_correct is None else self.manual_correct


@dataclass(frozen=True)
class GroupMetrics:
    cases: int
    within_deadline_rate: float
    avg_price_platforms: float
    accuracy: float


# ---- 評測集 ----

def load_cases(path: Path) -> List[EvaluationCase]:
    """讀取評測集（YAML 清單）。圖片路徑相對於評測集檔所在資料夾。"""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise EvaluationSetError(f"{path}: 評測集應為清單（每筆以 '- id:' 開頭）")

    cases: List[EvaluationCase] = []
    seen = set()
    for index, entry in enumerate(data, start=1):
        case = _parse_case(entry, index, path.parent)
        if case.id in seen:
            raise EvaluationSetError(f"[{case.id}] id 重複")
        seen.add(case.id)
        cases.append(case)
    return cases


def _parse_case(entry: Any, index: int, base_dir: Path) -> EvaluationCase:
    if not isinstance(entry, dict):
        raise EvaluationSetError(f"第 {index} 筆不是物件")
    case_id = str(entry.get("id") or "").strip()
    if not case_id:
        raise EvaluationSetError(f"第 {index} 筆缺少 id")

    def fail(message: str) -> EvaluationSetError:
        return EvaluationSetError(f"[{case_id}] {message}")

    given = entry.get("input")
    if not isinstance(given, dict) or ("text" in given) == ("image" in given):
        raise fail("input 必須恰好包含 text 或 image 其中之一")
    text = image = None
    image_path = ""
    if "text" in given:
        text = str(given["text"] or "").strip()
        if not text:
            raise fail("input.text 不可為空")
    else:
        image_path = str(given["image"] or "").strip()
        image_file = base_dir / image_path
        if not image_path or not image_file.is_file():
            raise fail(f"找不到圖片檔 {image_path}")
        image = image_file.read_bytes()

    try:
        category = Category(str(entry.get("category", "")).strip())
    except ValueError:
        choices = "、".join(c.value for c in Category)
        raise fail(f"category 必須是：{choices}") from None

    expected = entry.get("expected")
    if not isinstance(expected, dict):
        raise fail("缺少 expected")
    name = str(expected.get("name") or "").strip()
    model = str(expected.get("model") or "").strip()
    if not name:
        raise fail("缺少 expected.name（正確商品名稱）")
    if not model:
        raise fail("缺少 expected.model（型號規格）")
    urls = _string_list(expected.get("urls"))
    if not urls:
        raise fail("expected.urls 至少要有一個正確商品連結")
    match_terms = _string_list(expected.get("match_terms")) or (model,)

    source = entry.get("source") or {}
    source_text = " ".join(str(source[key]) for key in ("platform", "date") if source.get(key)) \
        if isinstance(source, dict) else str(source)

    return EvaluationCase(
        id=case_id,
        input_type=TEXT if text is not None else IMAGE,
        category=category,
        name=name,
        model=model,
        match_terms=match_terms,
        urls=urls,
        text=text,
        image=image,
        image_path=image_path,
        source=source_text,
    )


def _string_list(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, list) else [value]
    return tuple(str(item).strip() for item in items if str(item or "").strip())


# ---- 執行 ----

async def run_evaluation(
    cases: Iterable[EvaluationCase],
    *,
    compare: Compare,
    clock: Clock = system_clock,
) -> List[EvaluationRecord]:
    """
    逐筆（依序，不並行，避免互相搶資源影響耗時）呼叫比價流程入口。
    compare 為 compare_prices（可預先綁定 AI、平台集合、時鐘）；每筆使用獨立的空快取，確保跑完整流程。
    耗時以 clock 自呼叫入口起算，與正式服務「自收到訊息起算」一致。
    """
    records = []
    for case in cases:
        started = clock.monotonic()
        result: Optional[ComparisonResult] = None
        error = ""
        try:
            result = await compare(text=case.text, image=case.image, cache=TTLCache(), received_at=started)
        except Exception as exc:
            error = type(exc).__name__
            logger.warning(f"[Evaluation] {case.id}: comparison raised {error}: {exc}")
        elapsed = clock.monotonic() - started
        record = _record(case, result, elapsed, error)
        logger.info(
            f"[Evaluation] {case.id}: {elapsed:.2f}s, {record.price_platforms} priced, "
            f"correct={record.auto_correct}{' error=' + error if error else ''}"
        )
        records.append(record)
    return records


def _record(case: EvaluationCase, result: Optional[ComparisonResult], elapsed: float, error: str) -> EvaluationRecord:
    base = EvaluationRecord(
        case_id=case.id,
        input_type=case.input_type,
        category=case.category.value,
        query=case.text if case.text is not None else case.image_path,
        expected_name=case.name,
        expected_model=case.model,
        match_terms=case.match_terms,
        expected_urls=case.urls,
        source=case.source,
        elapsed_seconds=elapsed,
        error=error,
    )
    if result is None:
        return base
    return dataclasses.replace(
        base,
        ai_category=result.category.value,
        keyword_zh=result.keyword_zh,
        keyword_jp=result.keyword_jp,
        price_platforms=sum(q.status is PlatformStatus.OK for q in result.platforms.values()),
        platform_statuses={name: q.status.value for name, q in result.platforms.items()},
        platform_prices={name: q.min_price_twd for name, q in result.platforms.items()},
        ai_unavailable=result.ai_unavailable,
        from_cache=result.from_cache,
        auto_correct=is_match(case.match_terms, result),
    )


def is_match(match_terms: Sequence[str], result: ComparisonResult) -> bool:
    """
    自動判定：AI 理解後的搜尋關鍵字（中文或日文）包含全部比對詞即為正確。
    忽略大小寫、全半形、空白與標點（WH-1000XM5 = wh1000xm5）；英數結尾的比對詞後面緊接數字時不算吻合，
    以區分世代與容量（Switch ≠ Switch 2、230 ≠ 2300ml）；中文結尾的比對詞不受此限（青春露 = 青春露 230ml）。
    顏色不列入比對詞即不計顏色。
    AI 故障或判定與購物無關時沒有 AI 理解結果，一律不正確。
    """
    if result.ai_unavailable or result.parsed_item is None:
        return False
    keywords = [_normalize(result.keyword_zh), _normalize(result.keyword_jp)]
    return all(any(_contains_term(keyword, _normalize(term)) for keyword in keywords) for term in match_terms)


def _contains_term(keyword: str, term: str) -> bool:
    """
    term 出現在 keyword 中，且不是更長型號或數字的一部分（Switch ≠ Switch 2、230 ≠ 2300ml）。
    正規化會拿掉空白，「青春露 230ml」變成「青春露230ml」，所以只有英數結尾的 term 才檢查後面是否緊接數字。
    """
    start = keyword.find(term)
    checks_digit_after = bool(term) and term[-1].isascii() and term[-1].isalnum()
    while term and start != -1:
        end = start + len(term)
        digit_before = term[0].isdigit() and start > 0 and keyword[start - 1].isdigit()
        digit_after = checks_digit_after and end < len(keyword) and keyword[end].isdigit()
        if not digit_before and not digit_after:
            return True
        start = keyword.find(term, start + 1)
    return False


def _normalize(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text or "").lower() if ch.isalnum())


# ---- 指標 ----

def summarize(records: Sequence[EvaluationRecord]) -> Dict[str, Dict[str, GroupMetrics]]:
    """三項指標：整體（鍵「全部」）、依人工標註類別、依輸入型態。比對正確以人工覆核優先。"""
    by_category = {c.value: [r for r in records if r.category == c.value] for c in Category}
    by_input = {kind: [r for r in records if r.input_type == kind] for kind in (TEXT, IMAGE)}
    return {
        "overall": {"全部": _metrics(records)},
        "category": {name: _metrics(group) for name, group in by_category.items() if group},
        "input_type": {name: _metrics(group) for name, group in by_input.items() if group},
    }


def _metrics(records: Sequence[EvaluationRecord]) -> GroupMetrics:
    n = len(records)
    if n == 0:
        return GroupMetrics(0, 0.0, 0.0, 0.0)
    return GroupMetrics(
        cases=n,
        within_deadline_rate=sum(r.within_deadline for r in records) / n,
        avg_price_platforms=sum(r.price_platforms for r in records) / n,
        accuracy=sum(r.correct for r in records) / n,
    )


def format_summary(summary: Mapping[str, Mapping[str, GroupMetrics]]) -> str:
    titles = {"overall": "整體", "category": "依類別", "input_type": "依輸入型態"}
    lines = []
    for key, groups in summary.items():
        lines.append(f"== {titles.get(key, key)} ==")
        lines.append(f"{'分組':<12}{'筆數':>6}{'15秒內':>10}{'平均有價平台':>10}{'比對正確率':>10}")
        for name, m in groups.items():
            lines.append(
                f"{name:<12}{m.cases:>6}{m.within_deadline_rate:>10.1%}"
                f"{m.avg_price_platforms:>12.2f}{m.accuracy:>12.1%}"
            )
    return "\n".join(lines)


# ---- CSV ----

_RECORD_COLUMNS = (
    "case_id", "input_type", "category", "query", "expected_name", "expected_model", "match_terms",
    "expected_urls", "source", "ai_category", "keyword_zh", "keyword_jp", "elapsed_seconds",
    "within_deadline", "price_platforms", "ai_unavailable", "from_cache", "error", "auto_correct",
    "manual_correct",
)
_LIST_SEPARATOR = " | "


def _platform_columns() -> List[str]:
    return [f"{name}_{suffix}" for name in PLATFORM_POOL for suffix in ("status", "price_twd")]


def write_records_csv(records: Sequence[EvaluationRecord], path: Path) -> None:
    """每筆明細一列（UTF-8 BOM，Excel 可直接開）。manual_correct 留給人工覆核填寫。"""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*_RECORD_COLUMNS, *_platform_columns()])
        writer.writeheader()
        for r in records:
            row: Dict[str, Any] = {
                "case_id": r.case_id,
                "input_type": r.input_type,
                "category": r.category,
                "query": r.query,
                "expected_name": r.expected_name,
                "expected_model": r.expected_model,
                "match_terms": _LIST_SEPARATOR.join(r.match_terms),
                "expected_urls": _LIST_SEPARATOR.join(r.expected_urls),
                "source": r.source,
                "ai_category": r.ai_category,
                "keyword_zh": r.keyword_zh,
                "keyword_jp": r.keyword_jp,
                "elapsed_seconds": f"{r.elapsed_seconds:.3f}",
                "within_deadline": int(r.within_deadline),
                "price_platforms": r.price_platforms,
                "ai_unavailable": int(r.ai_unavailable),
                "from_cache": int(r.from_cache),
                "error": r.error,
                "auto_correct": int(r.auto_correct),
                "manual_correct": "" if r.manual_correct is None else int(r.manual_correct),
            }
            for name, status in r.platform_statuses.items():
                row[f"{name}_status"] = status
                price = r.platform_prices.get(name)
                row[f"{name}_price_twd"] = "" if price is None else price
            writer.writerow(row)


def read_records_csv(path: Path) -> List[EvaluationRecord]:
    """讀回明細 CSV（可能已人工填寫 manual_correct），用來重算指標。"""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [_record_from_row(row) for row in rows]


def _record_from_row(row: Mapping[str, str]) -> EvaluationRecord:
    case_id = row.get("case_id", "")
    statuses = {name: row[f"{name}_status"] for name in PLATFORM_POOL if row.get(f"{name}_status")}
    prices = {name: int(row[f"{name}_price_twd"]) if row.get(f"{name}_price_twd") else None for name in statuses}
    return EvaluationRecord(
        case_id=case_id,
        input_type=row["input_type"],
        category=row["category"],
        query=row.get("query", ""),
        expected_name=row.get("expected_name", ""),
        expected_model=row.get("expected_model", ""),
        match_terms=_split(row.get("match_terms", "")),
        expected_urls=_split(row.get("expected_urls", "")),
        source=row.get("source", ""),
        elapsed_seconds=float(row["elapsed_seconds"]),
        ai_category=row.get("ai_category", ""),
        keyword_zh=row.get("keyword_zh", ""),
        keyword_jp=row.get("keyword_jp", ""),
        price_platforms=int(row["price_platforms"]),
        platform_statuses=statuses,
        platform_prices=prices,
        ai_unavailable=_bool(row.get("ai_unavailable", "0"), case_id),
        from_cache=_bool(row.get("from_cache", "0"), case_id),
        error=row.get("error", ""),
        auto_correct=_bool(row["auto_correct"], case_id),
        manual_correct=_bool(row["manual_correct"], case_id) if (row.get("manual_correct") or "").strip() else None,
    )


def _split(value: str) -> Tuple[str, ...]:
    return tuple(part.strip() for part in value.split(_LIST_SEPARATOR.strip()) if part.strip())


def _bool(value: str, case_id: str) -> bool:
    word = (value or "").strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise EvaluationSetError(f"[{case_id}] 無法辨識的是非值：{value!r}（請填 1/0 或 是/否）")


def write_summary_csv(summary: Mapping[str, Mapping[str, GroupMetrics]], path: Path) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["group_by", "group", "cases", "within_15s_rate", "avg_price_platforms", "accuracy"])
        for key, groups in summary.items():
            for name, m in groups.items():
                writer.writerow([key, name, m.cases, f"{m.within_deadline_rate:.4f}",
                                 f"{m.avg_price_platforms:.4f}", f"{m.accuracy:.4f}"])


# ---- 命令列 ----

def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation.harness", description="比價機器人評測程式")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑評測集並輸出明細與指標")
    run.add_argument("set", type=Path, help="評測集 YAML 檔")
    run.add_argument("--out", type=Path, required=True, help="輸出資料夾（records.csv、summary.csv）")
    run.add_argument("--evaluation-mode", action="store_true",
                     help="啟用付費第三方 API（RapidAPI）轉接器做對照實驗；預設與正式服務相同")

    summarize_cmd = sub.add_parser("summarize", help="由（人工覆核後的）明細 CSV 重算指標")
    summarize_cmd.add_argument("records", type=Path, help="records.csv")
    summarize_cmd.add_argument("--out", type=Path, help="指標輸出檔（預設與明細同資料夾的 summary.csv）")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.command == "run":
        cases = load_cases(args.set)
        compare = functools.partial(compare_prices, platforms=build_platforms(evaluation_mode=args.evaluation_mode))
        records = asyncio.run(run_evaluation(cases, compare=compare))
        args.out.mkdir(parents=True, exist_ok=True)
        write_records_csv(records, args.out / "records.csv")
        summary_path = args.out / "summary.csv"
    else:
        records = read_records_csv(args.records)
        summary_path = args.out or args.records.with_name("summary.csv")

    summary = summarize(records)
    write_summary_csv(summary, summary_path)
    print(format_summary(summary))
    print(f"\n指標已寫入 {summary_path}")


if __name__ == "__main__":
    main()
