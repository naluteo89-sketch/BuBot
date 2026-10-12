"""기존 단독 실행 명령 호환: python embedding.py"""
from bubot.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["embed"]))
