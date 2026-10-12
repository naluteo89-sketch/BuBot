"""서버에서 공통 사이드바를 삽입해 페이지마다 디자인이 달라지지 않도록 합니다."""
from pathlib import Path

SIDEBAR = Path(__file__).resolve().parents[2] / "web" / "components" / "sidebar.html"


def render_sidebar(active):
    # 요청마다 읽으므로 템플릿 수정은 서버 재시작 없이 반영됩니다.
    markup = SIDEBAR.read_text(encoding="utf-8")
    for page in ("home", "chat", "history", "data"):
        attributes = ('class="nav-item active" aria-current="page"'
                      if page == active else 'class="nav-item"')
        markup = markup.replace("{{" + page + "_nav}}", attributes)
    return markup


def render_layout(markup, active):
    return markup.replace("<!-- APP_SIDEBAR -->", render_sidebar(active))
