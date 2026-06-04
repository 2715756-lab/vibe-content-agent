# Security Policy

## Supported versions

The project is early-stage. Security fixes target the main branch.

## Reporting a vulnerability

Do not open a public issue for secrets, token leaks, authentication bypasses, or
publishing vulnerabilities. Report privately to the repository maintainer.

## Sensitive data

Never commit:

- `.env` files;
- API keys or bot tokens;
- SQLite databases from a live instance;
- generated backups;
- private media;
- browser cookies or publishing sessions.

Use `.env.example` for documented configuration only.

## Deployment assumptions

Admin pages are intended to be protected by Basic Auth, a reverse proxy, VPN,
Cloudflare Access, or an equivalent private network boundary. Public routes are
limited to the blog, projects, media, RSS, sitemap, robots and health endpoints.
