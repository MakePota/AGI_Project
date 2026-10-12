"""Steam 工具里不依赖网络的纯逻辑。

真实链路（浏览器 + 登录态 + 真实账号）**不进自动测试**——它有真实副作用，
不该被 CI 反复触发。所以这里把能纯函数化的判断全部测到：appid 提取、价格渲染、
购物车条目匹配、工具注册。浏览器部分只能靠真实运行验证。

价格样例取自 2026-10-12 对 appdetails 的真实返回，不是编的。
"""

from __future__ import annotations

import pytest

from agi_agent.schemas import RiskLevel
from agi_agent.tools import get
from agi_agent.tools.builtin.steam import (
    CartItem,
    _appid_from_href,
    _cart_has,
    _format_price,
    _matching,
)

# ------------------------------------------------------------------ appid


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://store.steampowered.com/app/3681010/Nioh_3?snr=1_8_512_513", "3681010"),
        ("/app/570/", "570"),
        ("https://store.steampowered.com/search/?term=x", None),
        ("", None),
    ],
)
def test_appid_from_href(href, expected):
    assert _appid_from_href(href) == expected


# ------------------------------------------------------------------ 价格

#: 无折扣时 initial_formatted 是**空字符串**（实测）。
_NO_DISCOUNT = {
    "is_free": False,
    "price_overview": {
        "currency": "CNY",
        "initial": 34800,
        "final": 34800,
        "discount_percent": 0,
        "initial_formatted": "",
        "final_formatted": "¥ 348.00",
    },
    "release_date": {"coming_soon": False, "date": "5 Feb, 2026"},
}

_DISCOUNTED = {
    "is_free": False,
    "price_overview": {
        "currency": "CNY",
        "initial": 34800,
        "final": 27840,
        "discount_percent": 20,
        "initial_formatted": "¥ 348.00",
        "final_formatted": "¥ 278.40",
    },
}

_FREE = {"is_free": True, "price_overview": None}

_COMING_SOON = {
    "is_free": False,
    "price_overview": None,
    "release_date": {"coming_soon": True, "date": "2027"},
}


def test_price_without_discount_does_not_print_an_empty_original():
    """无折扣时 initial_formatted 是空串，不能照着模板渲染出「原价 」这种残句。"""
    text = _format_price("Nioh 3", "3681010", _NO_DISCOUNT)
    assert "¥ 348.00" in text
    assert "原价" not in text
    assert "3681010" in text


def test_price_with_discount_shows_both():
    text = _format_price("Nioh 3", "3681010", _DISCOUNTED)
    assert "原价 ¥ 348.00" in text
    assert "现价 ¥ 278.40" in text
    assert "20%" in text


def test_free_game():
    assert "免费" in _format_price("Dota 2", "570", _FREE)


def test_coming_soon_has_no_price():
    assert "尚未发售" in _format_price("X", "1", _COMING_SOON)


def test_missing_price_falls_back_loudly():
    assert "没有返回" in _format_price("X", "1", {})


# ------------------------------------------------------------------ 购物车匹配


def _item(appid: str | None, name: str | None) -> CartItem:
    return CartItem(appid=appid, name=name, price=None)


_CART = [_item("3681010", "Nioh 3"), _item("2627260", "NINJA GAIDEN 4")]


def test_cart_lookup_uses_appid_not_name():
    """用 appid 判断「是否已在购物车」——名称跨语言会变，appid 不会。"""
    assert _cart_has(_CART, "3681010")
    assert not _cart_has(_CART, "999999")


def test_removal_matching_is_exact():
    assert len(_matching(_CART, "Nioh 3")) == 1
    assert _matching(_CART, "Nioh") == []
    assert _matching(_CART, "Nioh 3 REDkit") == []


@pytest.mark.parametrize("written", ["Nioh 3", "nioh3", "Nioh ３", "Nioh　3"])
def test_removal_matching_tolerates_width_and_case(written):
    assert len(_matching(_CART, written)) == 1


def test_items_without_a_name_never_match():
    """名称是 None 的条目不能被空串匹配走，否则会误删。"""
    assert _matching([_item("1", None)], "") == []


# ------------------------------------------------------------------ 注册


def test_steam_tools_are_registered_with_expected_risk():
    expected = {
        "steam.search_games": RiskLevel.READ,
        "steam.list_cart": RiskLevel.READ,
        "steam.get_price": RiskLevel.READ,
        "steam.add_to_cart": RiskLevel.WRITE,
        "steam.remove_from_cart": RiskLevel.WRITE,
    }
    for name, risk in expected.items():
        assert get(name).risk is risk, name


def test_list_cart_takes_no_arguments():
    assert get("steam.list_cart").validate({}) == {}


def test_cart_tools_are_described_to_the_planner():
    """planner 只能看到签名与描述，描述不到位它就会猜。"""
    assert "list_cart" in get("steam.remove_from_cart").description
