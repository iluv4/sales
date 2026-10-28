import pytest

from sales_agent.config import ConfigError, load_config
from sales_agent.prompts import build_system_prompt


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_example_config_loads(tmp_path):
    from importlib import resources

    text = resources.files("sales_agent").joinpath("config.example.toml").read_text()
    config = load_config(write(tmp_path, text))
    assert config.agent.model == "claude-opus-5-5"
    assert config.db_file == tmp_path / "sales.db"


def test_unknown_keys_and_bad_effort_rejected(tmp_path):
    with pytest.raises(ConfigError, match="Unknown key"):
        load_config(write(tmp_path, "[company]\nnmae = 'x'\n"))
    with pytest.raises(ConfigError, match="Unknown section"):
        load_config(write(tmp_path, "[comapny]\n"))
    with pytest.raises(ConfigError, match="effort"):
        load_config(write(tmp_path, "[agent]\neffort = 'extreme'\n"))


def test_prompt_contains_config_and_is_stable(tmp_path):
    config = load_config(
        write(
            tmp_path,
            "[company]\nname = 'Widgetly'\nproof_points = ['Cut onboarding time 40% at Foo']\n"
            "[icp]\ndisqualifiers = ['Fewer than 10 employees']\n",
        )
    )
    prompt = build_system_prompt(config, web_research=True)
    assert "Widgetly" in prompt
    assert "- Fewer than 10 employees" in prompt
    assert "- Cut onboarding time 40% at Foo" in prompt
    assert "web search" in prompt
    assert prompt == build_system_prompt(config, web_research=True)
    assert "Web research is turned off" in build_system_prompt(config, web_research=False)
