"""BuBot: 문서화, 임베딩, 검색, 수치 검증과 RAG 답변."""
import sys

# 진입점(main.py·web_main.py)을 거치지 않는 import(테스트·스크립트)에서도 하위 패키지마다 __pycache__가 생기지 않게 합니다.
sys.dont_write_bytecode = True
