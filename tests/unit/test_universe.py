from data_pipeline.universe import Universe


def test_anchor_stocks_count_and_coverage():
    assert len(Universe.ANCHOR_STOCKS) == 30
    for code in Universe.ANCHOR_STOCKS:
        assert code.endswith(".SH") or code.endswith(".SZ")


def test_main_indices():
    assert set(Universe.MAIN_INDICES) == {"000300.SH", "000905.SH", "000016.SH", "399006.SZ"}


def test_sw_l1_indices_count():
    assert len(Universe.SW_L1_INDICES) == 31
    for code in Universe.SW_L1_INDICES:
        assert code.endswith(".SI")


def test_sw_industry_returns_index_code():
    # 600519.SH 贵州茅台 → 食品饮料 (801120.SI)
    assert Universe.sw_industry("600519.SH") == "801120.SI"


def test_sw_industry_unknown_returns_none():
    assert Universe.sw_industry("999999.SH") is None


def test_cs_universe_returns_list():
    cs = Universe.cs_universe()
    assert isinstance(cs, list)
    assert len(cs) >= 1
