const DEFAULT_MODEL = "@cf/stabilityai/stable-diffusion-xl-base-1.0";
const ALLOWED_MODELS = new Set([
  "@cf/stabilityai/stable-diffusion-xl-base-1.0",
  "@cf/bytedance/stable-diffusion-xl-lightning",
  "@cf/lykon/dreamshaper-8-lcm",
  "@cf/black-forest-labs/flux-1-schnell",
]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", model: imageModel(env) });
    }

    if (request.method !== "POST" || url.pathname !== "/") {
      return json({ error: "Not allowed" }, 405);
    }

    const auth = request.headers.get("Authorization");
    if (!env.API_KEY || auth !== `Bearer ${env.API_KEY}`) {
      return json({ error: "Unauthorized" }, 401);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "Body must be JSON" }, 400);
    }

    const prompt = String(body.prompt || "").trim();
    if (!prompt) {
      return json({ error: "Prompt is required" }, 400);
    }

    try {
      const result = await env.AI.run(imageModel(env), {
        prompt: prompt.slice(0, 2000),
        negative_prompt: "text, letters, watermark, logo, blurry, low quality",
        width: imageSize(env),
        height: imageSize(env),
        num_steps: Number(env.NUM_STEPS || 4),
      });
      const contentType = detectImageContentType(result);
      const imageBuffer = await new Response(result).arrayBuffer();
      return new Response(imageBuffer, {
        headers: {
          ...corsHeaders(),
          "Content-Type": contentType,
          "Cache-Control": "no-store",
        },
      });
    } catch (error) {
      return json({ error: "Failed to generate image", details: error.message }, 500);
    }
  },
};

function imageModel(env) {
  const configured = env.IMAGE_MODEL || DEFAULT_MODEL;
  return ALLOWED_MODELS.has(configured) ? configured : DEFAULT_MODEL;
}

function imageSize(env) {
  const size = Number(env.IMAGE_SIZE || 512);
  if (!Number.isFinite(size)) return 512;
  return Math.max(256, Math.min(1024, Math.round(size / 8) * 8));
}

function detectImageContentType(result) {
  const type = result?.type;
  if (typeof type === "string" && type.startsWith("image/")) {
    return type;
  }
  return "image/jpeg";
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...corsHeaders(),
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
  };
}
