import pytest

from apparitor.config import Config, GuildCfg, load_config


def test_unknown_key_is_a_clear_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("guilds = [1]\n[guild.1]\nreprot_names = true\n")
    with pytest.raises(SystemExit, match=r"guild\.1.*reprot_names"):
        load_config(str(p))
    p.write_text("guilds = [1]\n[admit]\nmin_edit = 5\n")
    with pytest.raises(SystemExit, match=r"admit.*min_edit"):
        load_config(str(p))


def test_defaults_and_for_guild(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('guilds = [1]\n[guild.1]\nreport_names = true\n[guild.1.roles]\nsysop = "A"\n')
    c = load_config(str(p))
    assert isinstance(c, Config) and c.dry_run and c.admit.reject_blocked
    assert c.for_guild(1).roles == {"sysop": "A"} and c.for_guild(1).report_names
    assert c.for_guild(2) == GuildCfg()
