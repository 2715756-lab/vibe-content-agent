from vibe_agent.media import build_image_prompt


def test_image_prompt_uses_title_as_scene_without_user_prompt():
    """Without an explicit prompt the article title drives the cover scene.

    Regression: covers used to be a generic "builder with AI agents" scene
    regardless of the title. Now the title (e.g. a story about mathematicians
    and AI) becomes the visual subject.
    """
    title = "Математики бьют тревогу: ИИ угрожает их профессии"
    prompt = build_image_prompt(title, "Краткое содержание про математику и ИИ.", None)

    # Title must shape the scene, not a generic builder workspace.
    assert title in prompt
    assert "builder working with AI agents" not in prompt
    # The directive to translate the title into a visual scene must be present.
    assert "translate the article title into a concrete visual scene" in prompt
    # Summary is still attached as context.
    assert "Краткое содержание" in prompt


def test_image_prompt_user_override_keeps_title_as_inspiration():
    """An explicit user prompt takes the scene, title stays as inspiration."""
    title = "Математики бьют тревогу: ИИ угрожает их профессии"
    prompt = build_image_prompt(title, "summary", "chalkboard full of equations, an AI hand erasing them")

    assert "chalkboard full of equations" in prompt
    assert title in prompt
    assert "semantic inspiration" in prompt


def test_image_prompt_empty_string_prompt_falls_back_to_title():
    """Empty/whitespace prompt is treated as no prompt -> title drives the scene."""
    title = "Новая модель GPT обходит бенчмарки"
    prompt = build_image_prompt(title, "", "   ")

    assert title in prompt
    assert "translate the article title into a concrete visual scene" in prompt
