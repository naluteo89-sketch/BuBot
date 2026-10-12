import re
"""사용자가 지정한 출처 표시명. 원본 파일 및 색인 메타데이터는 유지합니다."""
SOURCE_LABEL = "공공데이터포털, 한국부동산원_공동주택 실거래가격지수_아파트_월별 지역별 매매"


def display_sources(result):
    result = dict(result)
    if isinstance(result.get('answer'), str):
        result['answer'] = re.sub(r'(?:출처 문서 ID와 행 번호는 프로그램이 답변 뒤에 별도로 붙입니다\.?|(?:출처 문서 ID|출처)\s*[:：][^\n]*)', '', result['answer']).strip()
        result['answer'] = result['answer'].replace('new_prices.csv', SOURCE_LABEL).replace('data.csv', SOURCE_LABEL)
    if 'sources' in result:
        result['sources'] = [s.replace('new_prices.csv', SOURCE_LABEL).replace('data.csv', SOURCE_LABEL) for s in result['sources']]
    return result
