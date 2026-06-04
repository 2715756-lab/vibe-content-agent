from vibe_agent.collector import make_title, normalize_telegram_channel


def test_normalize_telegram_channel_from_username():
    assert normalize_telegram_channel("@durov") == "durov"


def test_normalize_telegram_channel_from_public_url():
    assert normalize_telegram_channel("https://t.me/s/durov") == "durov"


def test_make_title_truncates_long_text():
    title = make_title("x" * 140)
    assert len(title) == 113
    assert title.endswith("...")
