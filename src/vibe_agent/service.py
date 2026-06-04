from vibe_agent.apify import apify_config
from vibe_agent.collector import collect_sources
from vibe_agent.config import Settings
from vibe_agent.llm import clean_article_text, generate_draft, rewrite_compare_candidates, rewrite_draft
from vibe_agent.ranker import score_item
from vibe_agent.storage import Storage


class ContentAgent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = Storage(settings.database_path)

    async def collect_and_rank(self) -> dict:
        raw_items = await collect_sources(
            self.settings.sources_path,
            apify_config(self.storage.get_settings_map(["apify_api_token", "apify_enabled", "apify_timeout_seconds", "apify_max_items"])),
        )
        inserted = 0
        for item in raw_items:
            item["score"] = score_item(item, self.settings.keywords)
            if item.get("collector_type") == "apify":
                self.storage.save_apify_item(item)
            if self.storage.upsert_item(item):
                inserted += 1
        return {"fetched": len(raw_items), "inserted": inserted}

    async def draft(self, item_id: int, platform: str) -> dict:
        item = self.storage.get_item(item_id)
        if not item:
            raise ValueError(f"Item {item_id} not found")
        content = await generate_draft(item, platform, self.settings, ai_config=self.ai_config())
        draft_id = self.storage.save_draft(item_id, platform, content)
        return {"draft_id": draft_id, "platform": platform, "content": content}

    async def rewrite(
        self,
        draft_id: int,
        content: str | None = None,
        style_text: str | None = None,
        rewrite_instructions: str = "",
    ) -> dict:
        draft = self.storage.get_draft(draft_id)
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        source_content = clean_article_text(content if content is not None else draft["content"])
        if source_content != draft["content"]:
            self.storage.update_draft_content(draft_id, source_content)
        rewritten = await rewrite_draft(
            source_content,
            draft["platform"],
            self.settings,
            style_text=style_text,
            rewrite_instructions=rewrite_instructions,
            ai_config=self.ai_config(),
        )
        self.storage.update_draft_content(draft_id, rewritten)
        return {"draft_id": draft_id, "platform": draft["platform"], "content": rewritten}

    async def generate_variant(
        self,
        draft_id: int,
        platform: str,
        content: str,
        style_text: str | None = None,
    ) -> str:
        draft = self.storage.get_draft(draft_id)
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        source_content = clean_article_text(content)
        instructions = (
            "Сделай отдельную версию именно под эту площадку. "
            "Сохрани факты, но измени длину, структуру, подачу и тон под правила площадки. "
            "Не добавляй служебные фразы. Верни только готовый текст публикации."
        )
        variant = await rewrite_draft(
            source_content,
            platform,
            self.settings,
            style_text=style_text,
            rewrite_instructions=instructions,
            ai_config=self.ai_config(),
        )
        self.storage.upsert_draft_variant(draft_id, platform, variant)
        return variant

    async def compare_rewrites(
        self,
        draft_id: int,
        content: str,
        style_text: str | None = None,
        rewrite_instructions: str = "",
        limit: int = 3,
    ) -> list[dict]:
        draft = self.storage.get_draft(draft_id)
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        source_content = clean_article_text(content)
        if source_content != draft["content"]:
            self.storage.update_draft_content(draft_id, source_content)
        candidates = await rewrite_compare_candidates(
            source_content,
            draft["platform"],
            self.settings,
            style_text=style_text,
            rewrite_instructions=rewrite_instructions,
            ai_config=self.ai_config(),
            limit=limit,
        )
        for candidate in candidates:
            self.storage.add_draft_compare_variant(
                draft_id,
                candidate.get("label", ""),
                candidate.get("provider", ""),
                candidate.get("model", ""),
                candidate.get("content", ""),
                candidate.get("note", ""),
            )
        return candidates

    def ai_config(self) -> dict:
        return self.storage.get_settings_map(
            [
                "ai_text_provider",
                "openrouter_api_key",
                "openrouter_base_url",
                "openrouter_model",
                "gemini_api_key",
                "gemini_base_url",
                "gemini_model",
                "text_fallback_enabled",
                "openrouter_free_model",
                "huggingface_api_key",
                "huggingface_base_url",
                "huggingface_model",
                "openai_api_key",
                "openai_model",
                "custom_text_api_key",
                "custom_text_base_url",
                "custom_text_model",
            ]
        )
