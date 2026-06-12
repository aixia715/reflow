"""PCB badge 颜色映射的单元测试，包含对比度合规校验。"""
import pytest
from fastapi.testclient import TestClient

from app.badge_config import pcb_badge_class, PCB_TYPE_CLASS


# ── 单元测试：类名映射 ──────────────────────────────────────────────────


def test_known_types_return_correct_class():
    assert pcb_badge_class("电性件") == "badge--px-dianxingjian"
    assert pcb_badge_class("电性加强件") == "badge--px-dianxingqiangjiajian"
    assert pcb_badge_class("鉴定件") == "badge--px-jiandingjian"
    assert pcb_badge_class("正样") == "badge--px-zhengyang"
    assert pcb_badge_class("特殊") == "badge--px-special"


def test_unknown_types_return_unknown_class():
    assert pcb_badge_class("") == "badge--px-unknown"
    assert pcb_badge_class("V1.0") == "badge--px-unknown"
    assert pcb_badge_class("其他") == "badge--px-unknown"
    assert pcb_badge_class("PROTO") == "badge--px-unknown"


def test_all_mapped_types_have_badge_prefix():
    for css_class in PCB_TYPE_CLASS.values():
        assert css_class.startswith("badge--px-"), f"{css_class} 缺少 badge--px- 前缀"


# ── 对比度校验（WCAG AA >= 4.5:1）────────────────────────────────────


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _linearize(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    r, g, b = (_linearize(c) for c in _hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# 浅色主题颜色对（前景, 背景）
LIGHT_COLORS = {
    "电性件":     ("#0c5c48", "#d4f5ed"),
    "电性加强件": ("#92400e", "#fef3c7"),
    "鉴定件":     ("#4c1d95", "#ede9fe"),
    "正样":       ("#1e3a8a", "#dbeafe"),
    "特殊":       ("#9f1239", "#ffe4e6"),
    "unknown":    ("#374151", "#f3f4f6"),
}

# 深色主题颜色对
DARK_COLORS = {
    "电性件":     ("#6ee7b7", "#064e3b"),
    "电性加强件": ("#fcd34d", "#451a03"),
    "鉴定件":     ("#c4b5fd", "#2e1065"),
    "正样":       ("#93c5fd", "#1e3a8a"),
    "特殊":       ("#fda4af", "#4c0519"),
    "unknown":    ("#9ca3af", "#1f2937"),
}


@pytest.mark.parametrize("name,colors", LIGHT_COLORS.items())
def test_light_theme_contrast_wcag_aa(name: str, colors: tuple[str, str]):
    fg, bg = colors
    ratio = contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"浅色主题「{name}」对比度 {ratio:.2f}:1 低于 WCAG AA 要求（4.5:1）"
        f"，前景 {fg} 背景 {bg}"
    )


@pytest.mark.parametrize("name,colors", DARK_COLORS.items())
def test_dark_theme_contrast_wcag_aa(name: str, colors: tuple[str, str]):
    fg, bg = colors
    ratio = contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"深色主题「{name}」对比度 {ratio:.2f}:1 低于 WCAG AA 要求（4.5:1）"
        f"，前景 {fg} 背景 {bg}"
    )


# ── 路由集成测试：首页渲染正确的 badge 类名 ─────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _create_board(client: TestClient, pcb_version: str) -> None:
    client.post(
        "/board/new",
        data={
            "board_name": "测试板",
            "pcb_version": pcb_version,
            "bom_version": "B1",
            "board_uid": "SN001",
        },
        files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")},
        follow_redirects=False,
    )


@pytest.mark.parametrize("pcb_type,expected_class", [
    ("电性件", "badge--px-dianxingjian"),
    ("电性加强件", "badge--px-dianxingqiangjiajian"),
    ("鉴定件", "badge--px-jiandingjian"),
    ("正样", "badge--px-zhengyang"),
    ("特殊", "badge--px-special"),
    ("其他型号", "badge--px-unknown"),
])
def test_home_badge_class_by_pcb_type(client, pcb_type: str, expected_class: str):
    _create_board(client, pcb_type)
    r = client.get("/")
    assert r.status_code == 200
    assert expected_class in r.text, (
        f"首页未找到 PCB 类型「{pcb_type}」对应的 badge 类 {expected_class}"
    )
