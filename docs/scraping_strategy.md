# Scraping Strategy

Useful reference: https://github.com/lorien/awesome-web-scraping

## Priority order

1. Official APIs and RSS feeds.
2. Public pages with stable HTML and respectful rate limits.
3. Browser automation with Playwright only for owned accounts and review workflows.
4. Manual confirmation when a platform blocks automation or asks for captcha.

## Recommended tools

- `feedparser` for RSS/Atom sources.
- `httpx` + `BeautifulSoup` for simple public pages.
- Playwright for browser workflows such as preparing VC/Dzen drafts after the user has logged in.
- SQLite first, Qdrant/Chroma later when style and topic similarity become important.

## Captcha and anti-bot boundaries

The agent should not bypass captcha or evade anti-bot systems. If a platform presents a captcha,
the safe workflow is to pause, ask for manual confirmation, or use an official API/partner flow.

For our use case this is enough because the valuable automation is earlier in the pipeline:
finding topics, comparing relevance, drafting in the author's style, and preparing platform-specific text.
