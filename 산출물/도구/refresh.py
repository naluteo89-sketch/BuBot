"""코드와 문서를 읽어 자동 부록 및 로컬 HTML을 갱신한다. 업무 문서는 덮어쓰지 않는다."""
from pathlib import Path
import ast
import hashlib
import html
import json
import re
from datetime import datetime, timezone, timedelta
from markdown_it import MarkdownIt

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parent
NOW = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')

def write(rel, text):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')

def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '&#124;').replace('\n', '<br>')
    return '\n'.join('| ' + ' | '.join(map(cell, row)) + ' |' for row in [headers, ['---']*len(headers), *rows]) + '\n'

def files(base):
    found = []
    for folder in ('bubot', 'web', 'tests', 'knowledge'):
        found.extend(p for p in (base/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.js','.css','.html','.md'})
    found.extend(base/p for p in ('main.py','web_main.py','gpt_api.py','requirements.txt') if (base/p).exists())
    return {p.relative_to(base).as_posix():p for p in sorted(found)}

current = files(ROOT)
tracked = dict(current)
tracked['data/new_prices.csv'] = ROOT/'data/new_prices.csv'
manifest = {'generated_at':NOW, 'scope':'source, tests, knowledge, active CSV; no secret values or source excerpts',
            'files':{name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in tracked.items()}}
previous = OUT/'00_안내/기준정보.json'
if previous.exists():
    write('00_안내/기준정보_이전.json', previous.read_text(encoding='utf-8'))
write('00_안내/기준정보.json', json.dumps(manifest, ensure_ascii=False, indent=2))

classes=[]
functions=[]
tests=[]
class_details=[]
for name,p in current.items():
    if p.suffix!='.py': continue
    src=p.read_text(encoding='utf-8-sig')
    tree=ast.parse(src)
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and not name.startswith('tests/'):
            description = ast.get_docstring(node)
            if description and '원본 코드 참조' not in description:
                functions.append([name,node.lineno,node.name,description])
        if not isinstance(node,ast.ClassDef): continue
        if name.startswith('tests/'):
            for method in node.body:
                if isinstance(method,ast.FunctionDef) and method.name.startswith('test_'):
                    testid=f'{Path(name).stem}.{node.name}.{method.name}'
                    tests.append([testid,name,method.lineno])
            continue
        bases=', '.join(ast.unparse(b) for b in node.bases) or '없음'
        attrs=[]
        methods=[]
        for item in node.body:
            if isinstance(item,ast.AnnAssign): attrs.append(f'{ast.unparse(item.target)}: {ast.unparse(item.annotation)}')
            if isinstance(item,ast.FunctionDef):
                methods.append(f'{item.name}({ast.unparse(item.args)})')
                if item.name=='__init__':
                    for x in ast.walk(item):
                        if isinstance(x,ast.Attribute) and isinstance(x.ctx,ast.Store) and isinstance(x.value,ast.Name) and x.value.id=='self':
                            attrs.append(x.attr)
        classes.append([name,node.lineno,node.name,bases])
        class_details.append(f'## {node.name}\n\n파일: `{name}:{node.lineno}` / 상속: `{bases}`\n\n속성: '+', '.join(f'`{v}`' for v in dict.fromkeys(attrs))+'\n\n'+table(['메서드 서명'],[[m] for m in methods]) if methods else f'## {node.name}\n\n파일: `{name}:{node.lineno}` / 상속: `{bases}`\n\n속성: '+', '.join(f'`{v}`' for v in attrs)+'\n\n명시적 메서드 없음. 데이터 모델 생성·검증 동작은 데코레이터 또는 기반 클래스가 담당합니다.\n')
write('02_설계/클래스_자동목록.md','# 실제 클래스 자동 목록\n\n생성 시점: '+NOW+'\n\nAST로 추출한 선언이며 실행 연결 여부는 컴포넌트 설계서를 확인하세요.\n\n'+table(['파일','행','클래스','상속'],classes)+'\n'+'\n'.join(class_details))
write('03_구현/함수_자동목록.md','# 주요 모듈 함수 자동 목록\n\n생성 시점: '+NOW+'\n\n코드에 설명이 작성된 모듈 최상위 함수 목록입니다. 독립 함수의 존재가 사용자 기능 연결을 뜻하지는 않습니다.\n\n'+table(['파일','행','함수','코드 설명'],functions))
write('04_시험/자동시험_목록.md','# 자동시험 목록\n\n생성 시점: '+NOW+'\n\n'+f'현재 소스에 선언된 시험 메서드 {len(tests)}개. 이 목록은 실행 결과가 아닙니다. 실제 결과는 시험 결과서와 날짜별 로그를 확인하세요.\n\n'+table(['시험 식별자','파일','행'],tests))

md=MarkdownIt('commonmark',{'html':False}).enable('table')
pages=sorted(OUT.glob('*/*.md'))
pages.sort(key=lambda p:(0 if p.parent.name=='05_발표' else 1,str(p)))
slugs={p.resolve():f'doc-{i}' for i,p in enumerate(pages)}
sections=[]
nav=[]
for p in pages:
    text=p.read_text(encoding='utf-8')
    title=text.splitlines()[0].lstrip('# ')
    ident=slugs[p.resolve()]
    body=md.render(text)
    def fix_link(match):
        target=match.group(1)
        dest=(p.parent/target).resolve()
        if dest in slugs: return 'href="#'+slugs[dest]+'"'
        if not re.match(r'^[a-z]+:|^#',target):
            import os
            return 'href="'+html.escape(Path(os.path.relpath(dest,OUT)).as_posix(),quote=True)+'"'
        return match.group(0)
    body=re.sub(r'href="([^"]+)"',fix_link,body)
    sections.append(f'<section id="{ident}"><div class="meta">{html.escape(p.relative_to(OUT).as_posix())}</div>{body}</section>')
    nav.append(f'<a href="#{ident}">{html.escape(title)}</a>')
css='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#eef2f6;color:#17283b;font:16px/1.75 "Malgun Gothic",sans-serif}aside{position:fixed;inset:0 auto 0 0;width:260px;background:#152d46;color:white;padding:28px 20px;overflow:auto}aside h2{font-size:26px;margin:0}aside small{color:#b4ccdf}aside a{display:block;color:#dfebf5;text-decoration:none;font-size:13px;padding:8px 0;border-bottom:1px solid #ffffff18}input{width:100%;padding:10px;margin:20px 0;border:0;border-radius:5px}main{margin-left:260px;padding:36px;max-width:1500px}header,section{background:white;padding:36px 40px;margin-bottom:24px;border:1px solid #dbe3eb;border-radius:10px}header{border-top:6px solid #167e91}h1{font-size:30px;line-height:1.35;margin-top:10px}h2{font-size:21px;margin-top:30px;color:#15687b}h3{font-size:18px}p{max-width:100ch}table{width:100%;border-collapse:collapse;font-size:13px;margin:18px 0;table-layout:auto}th{background:#e8f1f6;text-align:left}td,th{padding:10px;border:1px solid #d3dfe8;overflow-wrap:anywhere;vertical-align:top}pre{background:#f1f5f8;padding:18px;overflow:auto;font:13px/1.65 Consolas,"Malgun Gothic",monospace;white-space:pre-wrap;overflow-wrap:anywhere}code{font-family:Consolas,"Malgun Gothic",monospace;overflow-wrap:anywhere}.meta{color:#6a7e90;font-size:12px}button{padding:9px 18px;background:#167e91;color:white;border:0;border-radius:5px;cursor:pointer}a{color:#12677b}@media(max-width:900px){aside{position:static;width:auto}main{margin:0;padding:12px}section,header{padding:20px}table{font-size:12px}}@media print{aside,button{display:none}main{margin:0;padding:0}section,header{border:0;padding:10px;break-before:page}table{font-size:10px}tr{break-inside:avoid}body{background:white}a{color:inherit}}'''
page='<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BuBot 팀 산출물</title><style>'+css+'</style><aside><h2>BuBot</h2><small>분석 · 설계 · 구현 · 시험</small><input id="q" aria-label="문서 검색" placeholder="문서 내용 검색"><nav>'+''.join(nav)+'</nav></aside><main><header><div class="meta">TEAM DOCUMENTATION · v1.0</div><h1>부동산 평균 매매가 조회 AI</h1><p>공개 양식의 핵심 항목을 적용한 팀 내부 검토용 산출물입니다. 발표 요약부터 상세 설계와 시험 증적까지 순서대로 확인하세요.</p><p class="meta">기준일 2026-10-12 · 편집 원본 Markdown · 업무 승인 전 초안</p><button onclick="window.print()">인쇄 / PDF 저장</button></header>'+''.join(sections)+'</main><script>document.querySelector("#q").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll("section").forEach(s=>{s.hidden=!s.textContent.toLowerCase().includes(q)});document.querySelectorAll("nav a").forEach(a=>{a.hidden=document.querySelector(a.getAttribute("href")).hidden})});</script></html>'
write('발표용_통합본.html',page)
print(json.dumps({'documents':len(pages),'classes':len(classes),'test_methods':len(tests),'html':str(OUT/'발표용_통합본.html')},ensure_ascii=False))
