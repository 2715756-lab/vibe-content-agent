# Roadmap

Vibe Content Agent is an early-stage self-hosted AI editorial lab. The roadmap
focuses on making AI-assisted publishing more transparent, reproducible, and
safe for independent creators, developers, and small teams.

## 1. Source Ingestion

- Improve duplicate detection across RSS, websites, Telegram mirrors, GitHub and Apify sources.
- Add source credibility scoring and source history.
- Add explainable ranking: why a topic was selected, what signals supported it, and what risks remain.

## 2. Editorial Agent

- Add structured editorial runs: research, angle selection, draft generation, fact-check, rewrite, final review.
- Store every rewrite version with rollback.
- Compare model outputs and keep an evaluation log.
- Improve style memory from approved articles.

## 3. Publishing

- Keep one auditable publication log across blog, RSS, Telegram, VK, VC and Zen workflows.
- Improve scheduled publishing and per-platform preview.
- Add safer browser-assisted publishing for platforms without public APIs.

## 4. Public Blog And Projects

- Improve article reactions, comments, sharing and analytics.
- Add public project demos with trial limits.
- Improve SEO, RSS, sitemap, `llms.txt`, structured data and AI search readiness.

## 5. Self-Hosted Operations

- Improve Docker and systemd deployment.
- Add Cloudflare Tunnel deployment guide.
- Add backup and restore tests.
- Add security checks for admin routes, tokens and media uploads.

## 6. OSS Maintenance

- Add more tests around storage migrations, publishing adapters and rewrite safety.
- Add small reproducible demo data.
- Document extension points for new collectors, LLM providers, image providers and publishers.
