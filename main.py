"""실행: python main.py --help"""
import sys

sys.dont_write_bytecode = True  # 패키지마다 __pycache__ 폴더가 생기지 않도록 (프로젝트 모듈 import 전에 설정)

from bubot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
