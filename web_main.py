"""로컬 테스트 웹 서버 실행 진입점."""
import sys

sys.dont_write_bytecode = True  # 패키지마다 __pycache__ 폴더가 생기지 않도록 (프로젝트 모듈 import 전에 설정)

from bubot.web.server import main
if __name__ == "__main__":
    main()
