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


def test_partial_block_is_not_block():
    u = {"name": "P", "groups": [], "blockid": 1, "blockpartial": True}
    i = wiki._pack(u, [])
    assert not i["blocked"] and i["blocked_partial"]
    assert wiki._pack({"name": "F", "groups": [], "blockid": 1}, [])["blocked"]


def test_valid_name():
    assert wiki.valid_name("Ле Лой")
    for bad in ("a|b", "", "  ", "x#y", "[[z]]", "a" * 300):
        assert not wiki.valid_name(bad)
