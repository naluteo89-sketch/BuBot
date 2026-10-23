**English** | [한국어](README.md)

# BuBot

BuBot is a conversational AI chatbot that answers natural-language questions about **average apartment sale prices in Korea**. It looks up and compares prices, and every answer comes with its calculation basis and data source.

The goal is to save users from hunting through complex price statistics tables and doing the math themselves.

---

## Development log

### Oct 7 — Planning and initial build
- [x] Wrote the first project plan and started development
- [x] Selected public datasets and converted them into keyword-indexed, structured data
- [x] Implemented natural-language processing with the GPT API
- [x] Tested wider search-condition ranges

### Oct 8 — Search improvements and conversation testing
- [x] Built and tested a single-turn (short-answer) chatbot
- [x] Wrote a search guide from test logs and refined search conditions
- [x] Expanded the public-data coverage after reviewing search results
- [x] Tested multi-turn conversation that remembers previous questions and search context
- [x] Added request delays to fix search-quota issues during back-to-back queries

### Oct 12 — Answer consistency and call throttling
- [x] Found and fixed the cause of inconsistent answers to the same question
- [x] Added fallback handling for low-confidence or error responses
- [x] Applied delays to the shared call path across the program
- [x] Re-verified all web and CLI features, including follow-up questions
- [x] Implemented search by landmarks and other major local facilities

### Oct 13 — Fixing earlier search errors and building test cases
- [x] Fixed previous search errors
- [x] Documented how to run the project and the test results
- [x] Saved the final version and checked accuracy and consistency results

### Oct 14 — Reducing hallucinations, search UI/UX
- [ ] Applied hallucination-reduction techniques
- [ ] Added question sub-categories for easier use

### Planned
- [ ] Chart-based visualization
- [ ] More datasets and APIs for broader search

---

## Screenshots

<img width="576" height="387" alt="Home screen" src="https://github.com/user-attachments/assets/e21da11c-2402-4d9c-9d14-c7d370b8dd97" />

**Home screen** — A question box, a price-unit selector, and example-question buttons. The traffic light at the bottom left checks the AI connection every 30 seconds and shows its status.

<img width="576" height="292" alt="Period average" src="https://github.com/user-attachments/assets/17867216-772f-4b4e-875c-de3ebd385ba2" />

**Conversation: period average** — For "Seoul's average sale price this year," BuBot answers with the simple average of the 8 months available (2026-01 to 2026-08). Each answer includes its source and an expandable "Check calculation and sources" section.

<img width="576" height="450" alt="Region comparison" src="https://github.com/user-attachments/assets/6bd20c2f-f635-4e82-a373-fe7b2136060d" />

**Conversation: region comparison** — Shows the prices of two regions for the same month and the difference between them. The left sidebar lists ongoing conversations and their question counts.

<img width="576" height="450" alt="Data guide" src="https://github.com/user-attachments/assets/13e8c0f4-aad0-4932-a0c6-0980a7f16471" />

**Data guide** — Four cards cover the available period (2012-01 to 2026-08), data sources, how to read prices, and how to ask follow-up questions. The "Past questions" screen shows your question history, and conversations can be saved as PDF.

---

## How it works

BuBot is a **hybrid RAG** chatbot: numbers are calculated by Python from the source data, and the LLM only explains them.

```
(memory) If there's earlier context, GPT rewrites the question as a standalone question
Question → rule checks (ambiguous region names, available years…) → GPT query analysis → router
  [Numeric path]  price lookup · comparison · trend · price ranking
     Fetch exact region/month rows from the source CSV by ID → calculate in Python
     + enrich with retrieved knowledge (region profiles, monthly market summaries) → GPT answer
  [Narrative path]  trend summaries · market overview · term/data explanations
     Vector search over the knowledge index → GPT answers only from retrieved documents
     If nothing passes the relevance threshold, GPT is not called and BuBot says it has no basis
```

- **~450 knowledge documents**: data guides, 245 region profiles, 176 monthly and 15 yearly market summaries. Every figure in them is computed by Python from the source CSV, none written by GPT.
- **Verifiable answers**: each answer shows the calculated values, the source rows and a SHA-256 source hash.
- **Number check**: percentage changes in an answer are compared with the evidence. If they don't match, the answer is rewritten once and flagged if it still differs.
- **Multi-turn memory**: remembers the last 3 Q&A pairs per session (in memory only, never on disk), so follow-ups like "What about Seocho?" work.
- **Data**: 245 regions, Jan 2012 – Aug 2026, from the Korea Real Estate Board (R-ONE) and data.go.kr.
- **Out of scope**: price forecasts, investment advice, jeonse/monthly rent, transaction volume, individual deals.

## Quick start (Windows / PowerShell)

Requires Python 3.12 and an OpenAI API key.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 1. Put the price data at data\new_prices.csv (not tracked in Git)
# 2. API key: set OPENAI_API_KEY, or keep it outside the repo in C:\keydata\gpt_key.py (open_Aikey = "...")
.\.venv\Scripts\python.exe main.py check-api   # shows whether the key is found (never prints it)

# 3. Build the knowledge index (calls the paid embedding API)
.\.venv\Scripts\python.exe main.py embed

# Web UI → http://127.0.0.1:8000   (add --lan to open it from a phone on the same Wi-Fi)
.\.venv\Scripts\python.exe web_main.py

# CLI
.\.venv\Scripts\python.exe main.py ask "서울 2026년 8월 평균매매가격 알려줘"
.\.venv\Scripts\python.exe main.py chat
```

Tests run without calling the paid API:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

## Documentation (Korean)

- [`실행방법.txt`](실행방법.txt) — full setup, configuration (environment variables), troubleshooting
- [`파일별_기능.md`](파일별_기능.md) — what each file and function does
- [`knowledge/`](knowledge/) — data guide, terms and calculation methods, FAQ

## Tech stack

Python · OpenAI API (GPT, `text-embedding-3-small`, structured outputs) · NumPy · Pydantic · SQLite vector store · vanilla HTML/CSS/JS

> BuBot works with Korean apartment price data, so its interface and answers are in Korean.
