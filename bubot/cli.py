"""명령행 실행만 담당합니다. 문서/임베딩/검색/AI 답변 로직은 별도 모듈에 있습니다."""
import argparse
import json

from .config import DOCUMENTS_PATH, KNOWLEDGE_DOCUMENTS_PATH, DISPLAY_PRICE_UNIT, DISPLAY_PRICE_UNITS, KNOWLEDGE_MAX_DISTANCE


def _add_query_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("question", help="자연어 질문")
    parser.add_argument("--top-k", type=int, default=None,
                        help="서술형 질문의 지식 문서 의미 검색 개수 (기본 4, 1~50)")
    parser.add_argument("--region-id", help="지역 ID로 결과 제한")
    parser.add_argument("--month", help="기준 월 YYYY-MM")
    parser.add_argument("--kind", choices=["monthly", "timeline"], help="문서 종류")
    parser.add_argument("--unit", choices=DISPLAY_PRICE_UNITS, default=None,
                        help=f"가격 표시 단위 (미지정 시 질문 분석/기본값 {DISPLAY_PRICE_UNIT})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BuBot 하이브리드 RAG: 문서 준비·지식 색인 임베딩·검색·AI 답변"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("prepare", help="CSV 검증, 가격 문서·지식 문서 생성 (API 미사용)")
    commands.add_parser("embed", help="RAG 지식 색인 임베딩 (질문 응답에 필요, 약 450개 문서)")
    commands.add_parser("check-api", help="API 호출 없이 키 인식 여부와 설정 파일 경로 확인")

    query = commands.add_parser("search", help="RAG 지식 색인 의미 검색 결과 확인")
    query.add_argument("question", help="자연어 질문")
    query.add_argument("--top-k", type=int, default=5, help="검색 상위 개수 (기본 5)")
    query.add_argument("--all", action="store_true", help="관련도 임계값을 넘는 문서도 표시")

    ask = commands.add_parser("ask", help="검색 결과를 기반으로 AI(LLM)가 분석 답변 생성")
    _add_query_options(ask)
    ask.add_argument("--show-analysis", action="store_true",
                     help="LLM의 질의 분석 결과(JSON)를 함께 출력")
    ask.add_argument("--show-contexts", action="store_true",
                     help="LLM에 전달된 검색 문서를 함께 출력")
    ask.add_argument("--show-retrieval", action="store_true",
                     help="지식 색인 검색 결과(사용/제외 문서와 거리)를 함께 출력")
    chat = commands.add_parser("chat", help="대화형 모드: 최근 질문·답변(최소 3개)을 기억하며 이어서 질문")
    chat.add_argument("--unit", choices=DISPLAY_PRICE_UNITS, default=None, help="가격 표시 단위")
    return parser


def cmd_check_api(_args) -> int:
    from .llm.gpt_api import check_configuration
    print(json.dumps(check_configuration(), ensure_ascii=False, indent=2))
    return 0


def cmd_prepare(_args) -> int:
    from .indexing.documents import read_corpus, export_documents
    from .indexing.knowledge import build_knowledge
    from collections import Counter
    corpus = read_corpus()
    export_documents(corpus, DOCUMENTS_PATH)
    knowledge = build_knowledge(corpus)
    export_documents(knowledge, KNOWLEDGE_DOCUMENTS_PATH)
    result = {
        "지역": corpus.region_count,
        "월": len(corpus.months),
        "결측": corpus.missing_count,
        "가격 문서": len(corpus.documents),
        "지식 문서": len(knowledge.documents),
        "지식 문서 종류": dict(Counter(d.metadata["kind"] for d in knowledge.documents)),
        "출력": [str(DOCUMENTS_PATH), str(KNOWLEDGE_DOCUMENTS_PATH)],
        "API 호출": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_embed(_args) -> int:
    from .embedding.knowledge_index import build_knowledge_index
    print(json.dumps(build_knowledge_index(), ensure_ascii=False, indent=2))
    return 0


def cmd_search(args) -> int:
    from .embedding.knowledge_index import retrieve
    from .rag_service import open_index
    _, collection = open_index()
    hits, diagnostics = retrieve(collection, args.question, general_k=args.top_k,
                                 max_distance=2.0 if args.all else KNOWLEDGE_MAX_DISTANCE)
    print(json.dumps({"diagnostics": diagnostics,
                      "hits": [{k: h[k] for k in ("id", "distance", "pool", "text")} for h in hits]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_ask(args) -> int:
    from .conversation import ask
    result = ask(args.question, top_k=args.top_k, region_id=args.region_id,
                 month=args.month, kind=args.kind, unit=args.unit)
    if args.show_analysis:
        print(json.dumps(result['analysis'], ensure_ascii=False, indent=2))
    if args.show_contexts:
        print(json.dumps(result['contexts'], ensure_ascii=False, indent=2))
    if args.show_retrieval:
        print(json.dumps(result['analysis'].get('retrieval'), ensure_ascii=False, indent=2))
    print("\n[AI 답변]\n" + result['answer'])
    if result['facts']:
        print("\n[원본 데이터 계산]\n" + "\n".join(result['facts']))
    if result['sources']:
        print("\n[근거]\n" + "\n".join(result['sources']))
    return 0


def cmd_chat(args) -> int:
    from uuid import uuid4
    from .conversation import MEMORY, chat
    session = uuid4().hex
    print(f"BuBot 대화 모드 (최근 {MEMORY.turns}개 질문·답변 기억). 종료: exit, 새 대화: /new")
    while True:
        try:
            question = input("\n질문> ").strip()
        except EOFError:
            return 0
        if question.lower() in {"exit", "quit", "종료"}:
            return 0
        if question == "/new":
            MEMORY.reset(session)
            session = uuid4().hex
            print("새 대화를 시작합니다.")
            continue
        if not question:
            continue
        try:
            result = chat(session, question, unit=args.unit)
        except ValueError as error:
            print(f"처리 안내: {error}")
            continue
        if result.get("followup"):
            print(f"[이전 대화 반영] {result['standalone_question']}")
        print("\n[AI 답변]\n" + result["answer"])
        if result["facts"]:
            print("\n[원본 데이터 계산]\n" + "\n".join(result["facts"]))
        if result["sources"]:
            print("\n[근거]\n" + "\n".join(result["sources"]))
        print(f"(대화 메모리 {result['memory_turns']}개)")


_HANDLERS = {
    "check-api": cmd_check_api,
    "prepare": cmd_prepare,
    "embed": cmd_embed,
    "search": cmd_search,
    "ask": cmd_ask,
    "chat": cmd_chat,
}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.error(f"알 수 없는 커맨드: {args.command}")

    try:
        return handler(args)
    except (OSError, ValueError, ImportError) as error:
        print(f"실행 실패: {error}")
        return 1
    except KeyboardInterrupt:
        print("\n사용자 중단")
        return 130
    except Exception as error:
        print(f"API 또는 저장소 처리 실패 ({type(error).__name__}). 키 권한·사용 한도·네트워크를 확인하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
