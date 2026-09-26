from apparitor import wiki


def test_pack_local_and_global_groups():
    u = {
        "name": "Base",
        "groups": ["*", "user", "editor", "rollbacker"],
        "editcount": 5,
        "registration": "2011-05-19T19:07:22Z",
    }
    i = wiki._pack(u, ["steward", "abusefilter-helper"])
    assert i["groups"] == ["editor", "rollbacker", "steward"]
    assert "стюард" in i["labels"] and i["apat"] and not i["sysop"] and not i["blocked"]


def test_statuses_from_gadget_json():
    content = '{"userSet": {"A": ["Adm"], "I+": ["Swarrel"], "V": ["Swarrel", "MBH"], "Ar": ["Adm"]}, "x": 1}'
    st = wiki.parse_statuses(content)
    assert st == {"Swarrel": {"closer-plus", "vandalfighter"}, "MBH": {"vandalfighter"}, "Adm": {"arbcom"}}
    u = {"name": "Swarrel", "groups": ["user", "closer", "editor"]}
    i = wiki._pack(u, ["steward"], st["Swarrel"])
    assert i["groups"] == ["closer", "editor", "closer-plus", "vandalfighter", "steward"]
    assert "полномочный ПИ" in i["labels"]
    # арбитр-админ: в группе рувики уже есть arbcom — не дублируем
    i = wiki._pack({"name": "Adm", "groups": ["sysop", "arbcom"]}, [], st["Adm"])
    assert i["groups"] == ["sysop", "arbcom"]


def test_partial_block_is_not_block():
    u = {"name": "P", "groups": [], "blockid": 1, "blockpartial": True}
    i = wiki._pack(u, [])
    assert not i["blocked"] and i["blocked_partial"]
    assert wiki._pack({"name": "F", "groups": [], "blockid": 1}, [])["blocked"]
    i = wiki._pack({"name": "L", "groups": []}, [], locked=True)
    assert i["blocked"] and i["locked"], "глобальный lock = отказ"


def test_wmf_staff_by_name_or_global_group():
    assert "wmf" in wiki._pack({"name": "NForrester (WMF)", "groups": []}, [])["groups"]
    i = wiki._pack({"name": "Someone", "groups": []}, ["wmf-legal"])
    assert i["groups"] == ["wmf"] and "сотрудник WMF" in i["labels"]
    assert "wmf" not in wiki._pack({"name": "Carn", "groups": ["editor"]}, ["abusefilter-helper"])["groups"]


def test_norm_name_like_mediawiki():
    assert wiki.norm_name("bezik") == "Bezik"
    assert wiki.norm_name("alex_nb_it") == "Alex nb it"
    assert wiki.norm_name("Ле Лой") == "Ле Лой"
    assert wiki.norm_name("ghuron ") == "Ghuron"


def test_valid_name():
    assert wiki.valid_name("Ле Лой")
    for bad in ("a|b", "", "  ", "x#y", "[[z]]", "a" * 300):
        assert not wiki.valid_name(bad)
