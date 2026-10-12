"""Steam 工具的名称归一化。

名称匹配是这个工具里最容易出错、且后果最实在的一环——它会真实改动账号的购物车，
而审批发生在执行之前，用户批准的只是意图。这里只锁归一化规则，它是完全同名校验的基石。
"""

from __future__ import annotations

import pytest

from agi_agent.tools.builtin.steam import _normalize


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Portal 2", "Portal2"),
        ("Portal 2", "portal 2"),
        ("The Witcher 3: Wild Hunt", "thewitcher3wildhunt"),
        ("Half-Life 2", "Half Life 2"),
        ("Baldur's Gate 3", "BaldursGate3"),
    ],
)
def test_equivalent_names_normalize_the_same(left, right):
    assert _normalize(left) == _normalize(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Portal", "Portal 2"),
        ("Portal", "Portal Knights"),
        ("Half", "Half-Life: Alyx"),
        ("The Witcher 3", "The Witcher 3: Wild Hunt"),
    ],
)
def test_prefixes_do_not_normalize_to_the_same(left, right):
    """前缀必须不相等，否则 "Portal" 会被当成 "Portal Knights"。"""
    assert _normalize(left) != _normalize(right)
