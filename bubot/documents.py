import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

# 설정 상수 (프로젝트 상황에 맞게 조절)
from .config import CSV_PATH, DOCUMENT_VERSION
from .pricing import format_price
REGION_COL_COUNT = 4  # 지역 계층열 개수 (1: ID, 1~4: 지역명)
DATA_START_COL = 1 + REGION_COL_COUNT  # 5번째 인덱스부터 월 데이터


@dataclass(frozen=True)
class Document:
    id: str
    text: str
    metadata: dict


@dataclass(frozen=True)
class Corpus:
    documents: list[Document]
    source_hash: str
    region_count: int
    months: tuple[str, ...]
    missing_count: int


def _format_price_for_llm(price_thousand: Decimal) -> str:
    return f"{format_price(price_thousand, '천원')} ({format_price(price_thousand)})"


def read_corpus(path: Path = CSV_PATH) -> Corpus:
    raw = Path(path).read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 4 or len(rows[0]) < (DATA_START_COL + 1):
        raise ValueError(f"최소 {REGION_COL_COUNT}개 지역열, 1개 이상의 월별 가격, 3행의 헤더가 필요합니다.")

    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("CSV 행별 열 개수가 일치하지 않습니다.")

    # 1. 월 헤더 검증
    months = []
    for value in rows[0][DATA_START_COL:]:
        match = re.fullmatch(r"(\d{4})년\s*(\d{1,2})월", value.strip())
        if not match or not 1 <= int(match[2]) <= 12:
            raise ValueError(f"월 헤더 형식 오류: {value}")
        months.append(f"{match[1]}-{int(match[2]):02d}")

    if months != sorted(set(months)):
        raise ValueError("월 헤더는 중복 없이 시간순이어야 합니다.")

    # 2. 단위 헤더 검증
    if any(v.strip() != "천원" for v in rows[2][DATA_START_COL:]):
        raise ValueError("CSV 가격 단위는 모두 '천원'이어야 합니다.")

    documents, seen_ids, seen_paths = [], set(), set()
    missing_count = 0

    # 3. 데이터 행 파싱
    for row_number, row in enumerate(rows[3:], start=4):
        rid = row[0].strip()
        hierarchy = [part.strip() for part in row[1:DATA_START_COL]]

        if not rid or any(not part for part in hierarchy):
            raise ValueError(f"{row_number}행: 지역 ID 또는 지역명 누락")

        # 계층 중복 제거 및 경로 생성
        label = " / ".join(dict.fromkeys(hierarchy))

        if rid in seen_ids or label in seen_paths:
            raise ValueError(f"{row_number}행: 지역 ID('{rid}') 또는 경로('{label}') 중복")

        seen_ids.add(rid)
        seen_paths.add(label)

        metadata = {
            "source": Path(path).name,
            "source_sha256": source_hash,
            "source_row": row_number,
            "region_id": rid,
            "region_path": label,
            "region_name": hierarchy[-1],
            "unit": "천원",
            "document_version": DOCUMENT_VERSION,
            **{f"region_{i+1}": val for i, val in enumerate(hierarchy)},
        }

        timeline = []
        region_missing = 0

        for month, raw_price in zip(months, row[DATA_START_COL:]):
            cleaned = raw_price.strip().replace('"', "").replace(",", "")
            missing = not cleaned

            if missing:
                price_text = "데이터 없음 (결측)"
                region_missing += 1
                price_metadata = {}
            else:
                try:
                    price = Decimal(cleaned)
                except InvalidOperation:
                    raise ValueError(f"{row_number}행 {month}: 가격 숫자 형식 오류 ({cleaned})") from None

                if not price.is_finite() or price < 0:
                    raise ValueError(f"{row_number}행 {month}: 유효하지 않은 가격 ({price})")

                price_text = _format_price_for_llm(price)
                price_metadata = {"price_thousand_krw": float(price)}

            timeline.append(f"{month}: {price_text}")

            # LLM이 읽을 단일 월 문서 생성
            document_text = (
                f"아파트 평균매매가격\n"
                f"지역: {label}\n"
                f"기준 월: {month}\n"
                f"가격: {price_text}\n"
                f"출처: {Path(path).name} (행 번호: {row_number})"
            )

            documents.append(
                Document(
                    id=f"region-{rid}-month-{month}",
                    text=document_text,
                    metadata={
                        **metadata,
                        **price_metadata,
                        "kind": "monthly",
                        "month": month,
                        "missing": missing,
                    },
                )
            )

        missing_count += region_missing

        # 타임라인 통합 문서 생성
        timeline_text = (
            f"아파트 평균매매가격 월별 추이\n"
            f"지역: {label}\n"
            + "\n".join(timeline)
            + f"\n출처: {Path(path).name} (행 번호: {row_number}, 결측치 보간 안 됨)"
        )

        documents.append(
            Document(
                id=f"region-{rid}-timeline",
                text=timeline_text,
                metadata={
                    **metadata,
                    "kind": "timeline",
                    "start_month": months[0],
                    "end_month": months[-1],
                    "missing_count": region_missing,
                },
            )
        )

    return Corpus(documents, source_hash, len(seen_ids), tuple(months), missing_count)


def export_documents(corpus: Corpus, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for document in corpus.documents:
            stream.write(json.dumps(asdict(document), ensure_ascii=False) + "\n")
    temporary.replace(output)
