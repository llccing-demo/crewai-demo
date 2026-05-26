## 

notes
- https://github.com/crewAIInc/crewAI

## What was built

**Environment:** Installed `uv` + Python 3.12 + CrewAI 1.14.5 (with all tools).

**Project structure:**
```
crewai-demo/
├── .env.example          ← copy to .env and fill in API keys
├── pyproject.toml
└── src/blog_improver/
    ├── main.py           ← CLI entry point
    ├── crew.py           ← 2 crew definitions
    └── config/
        ├── agents.yaml   ← 6 agents (3 per crew, all in Chinese)
        └── tasks.yaml    ← 6 tasks (3 per crew)
```

**Crew 1 — Blog Topic Strategist** (`suggest` command):
- `blog_analyst` scrapes your RSS feed → maps all 100+ post topics
- `trend_researcher` uses Serper web search → finds 2026 frontend/AI hot topics you haven't covered
- `content_strategist` synthesizes → outputs **5 structured Chinese article ideas** to `suggestions.md`

**Crew 2 — SEO & Quality Reviewer** (`review` command):
- `article_reader` scrapes any post URL you provide
- `seo_specialist` checks titles, heading structure, keyword density, internal links
- `technical_editor` checks completeness, code examples, readability → outputs full **Chinese review report** to `review.md`

**Crew 3 — URL Translator With Coverage Check** (`translate` command):
- `content_fetcher` fetches a URL into a segmented source package and runs a fetch completeness check
- `translation_specialist` translates each segment to Chinese instead of trying to translate one huge page in a single prompt
- `translation_verifier` confirms every segment is present and flags likely omissions or summarization
- artifacts are written under `src/blog_improver/results/translations/<model>/<run>/`

---

## Next steps to run it

1. Copy .env.example → `.env` and add your keys:
   - `OPENAI_API_KEY` from [platform.openai.com](https://platform.openai.com/api-keys)
   - `SERPER_API_KEY` from [serper.dev](https://serper.dev) (free tier)

2. Run Crew 1 (topic suggestions):
   ```
   uv run python -m blog_improver.main suggest
   ```

3. Run Crew 2 (SEO review of any post):
   ```
   uv run python -m blog_improver.main review --url https://rowanliu.com/posts/angular-signals-zoneless/
   ```

4. Run Crew 3 (fetch + translate + verify any URL):
   ```
   uv run python -m blog_improver.main translate --url https://example.com/article
   ```

   This workflow writes:
   - `source.md` and `source_segments.md`
   - `translation.md`
   - `fetch_report.md`
   - `verification.md`

Made changes.