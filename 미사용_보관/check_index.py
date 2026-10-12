"""색인 불일치 진단 (API 호출 없음, 파일 수정 없음).
실행: .\.venv\Scripts\python.exe check_index.py
"""
import json
import sys

import gpt_api
from bubot import config
from bubot.documents import read_corpus
from bubot.embedding import index_identity

print("Python:", sys.version.split()[0], "|", sys.executable)
print("CSV_PATH:", config.CSV_PATH, "| 존재:", config.CSV_PATH.exists())
print("MANIFEST:", config.MANIFEST_PATH)

manifest = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
corpus = read_corpus(config.CSV_PATH)
current = index_identity(corpus, gpt_api.embedding_spec())

mismatch = False
for key in sorted(set(manifest) | set(current)):
    same = manifest.get(key) == current.get(key)
    mismatch |= not same
    print(f"[{'OK' if same else '다름'}] {key}")
    if not same:
        print("    색인:", manifest.get(key))
        print("    현재:", current.get(key))

if mismatch:
    sample = corpus.documents[0]
    print("\n첫 문서 확인:", sample.id)
    print(json.dumps({"text": sample.text, "metadata": sample.metadata}, ensure_ascii=False, indent=2))
else:
    print("\n색인 일치. 웹에서 계속 오류가 나면 이전에 켜 둔 웹 서버가 아직 응답하고 있는 것입니다.")
