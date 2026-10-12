from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, StringConstraints

# YYYY-MM 형식만 허용합니다. OpenAI 구조화 출력 스키마에도 pattern으로 전달되어 '2023' 같은 값이 나오지 않습니다.
Month = Annotated[str, StringConstraints(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$')]


class Analysis(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    intent: Literal['lookup','trend','compare','argmax','argmin','explain','clarify']
    region_names: list[str]
    month: Month | None
    start_month: Month | None
    end_month: Month | None
    display_unit: Literal['천원','만원','억원','korean'] | None
    level: Literal['시도','시군구','권역']
    rewritten_query: str
    message: str


class Condensed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    is_followup: bool
    standalone_question: str
