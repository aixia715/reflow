"""登录页的浏览器测试。不用 conftest 里已登录的 context，否则看不到登录闸门。"""

import re

from playwright.sync_api import expect


def _channels(rgb: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", rgb)][:3]


def test_anonymous_lands_on_login_and_bad_password(live_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/")
    expect(page).to_have_url(re.compile(r"/login"))
    page.locator("input[name=username]").fill("admin")
    page.locator("input[type=password]").fill("wrong-password")
    page.locator("button[type=submit]").click()
    expect(page.locator(".flash-error")).to_contain_text("用户名或密码错误")
    context.close()


def test_login_password_contrast_and_narrow_viewport(live_server, browser):
    context = browser.new_context(viewport={"width": 375, "height": 700})
    page = context.new_page()
    page.goto(live_server + "/login")
    page.evaluate("localStorage.setItem('theme','light')")
    page.reload()
    el = page.locator("input[type=password]")
    bg = el.evaluate("e => getComputedStyle(e).backgroundColor")
    fg = el.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(bg)) > 600
    assert sum(_channels(fg)) < 300
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    page.evaluate("localStorage.setItem('theme','dark')")
    page.reload()
    el = page.locator("input[type=password]")
    bg = el.evaluate("e => getComputedStyle(e).backgroundColor")
    fg = el.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(bg)) < 250, bg
    assert sum(_channels(fg)) > 350, fg
    context.close()
