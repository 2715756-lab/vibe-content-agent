# Vibe Image Worker

Cloudflare Worker для бесплатной генерации картинок через Workers AI. Он совместим с провайдером `Cloudflare Worker Images` в основном приложении.

Основа идеи: `saurav-z/free-image-generation-api`, но worker здесь чуть адаптирован под наш агент: есть `/health`, CORS, ограничение моделей и понятные ошибки.

## Деплой

```bash
cd cloudflare-image-worker
npm install
npx wrangler login
npx wrangler secret put API_KEY
npm run deploy
```

В `API_KEY` вставь любой длинный секрет. Его же потом нужно указать в настройках агента в поле `Cloudflare Worker API key`.

После деплоя Wrangler покажет URL вида:

```text
https://vibe-image-worker.<subdomain>.workers.dev
```

Этот URL нужно вставить в настройках агента в поле `Cloudflare Worker URL`.

## Проверка

```bash
curl https://vibe-image-worker.<subdomain>.workers.dev/health
```

Генерация:

```bash
curl -X POST https://vibe-image-worker.<subdomain>.workers.dev \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Editorial cover image about AI coding agents, no text, no logos"}' \
  --output image.jpg
```

## Модель

По умолчанию:

```text
@cf/stabilityai/stable-diffusion-xl-base-1.0
```

Модель можно поменять в `wrangler.toml`:

```toml
[vars]
IMAGE_MODEL = "@cf/bytedance/stable-diffusion-xl-lightning"
```

В worker разрешены только модели из списка `ALLOWED_MODELS` в `src/worker.js`.

## Подключение к агенту

Открой:

```text
http://127.0.0.1:8088/settings
```

В блоке `AI-оператор картинок` выбери:

```text
Cloudflare Worker Images
```

И заполни:

- `Cloudflare Worker URL`
- `Cloudflare Worker API key`

После этого кнопка `Сгенерировать картинку` на странице черновика будет ходить в этот worker.
