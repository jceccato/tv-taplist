"""Theme preset resolution and custom-colour validation."""
from app.theme import DEFAULT_THEME, THEME_KEYS, THEMES, resolve_theme


def test_presets_define_every_colour_key():
    for name, spec in THEMES.items():
        for key in THEME_KEYS:
            assert key in spec["colors"], f"{name} missing {key}"


def test_resolve_named_preset_returns_fixed_palette():
    assert resolve_theme({"theme": "oled"})["bg"] == "#000000"
    assert resolve_theme({"theme": "default"}) == DEFAULT_THEME


def test_resolve_unknown_theme_falls_back_to_default():
    assert resolve_theme({"theme": "made-up"}) == DEFAULT_THEME
    assert resolve_theme({}) == DEFAULT_THEME


def test_resolve_custom_uses_overrides_and_fills_gaps():
    colors = resolve_theme({"theme": "custom", "theme_custom": {"bg": "#101010"}})
    assert colors["bg"] == "#101010"                 # honoured
    assert colors["text"] == DEFAULT_THEME["text"]   # missing -> default
