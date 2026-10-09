"""Real Chromium functional and responsive checks against the backend."""

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect


async def run(url, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    failures = []
    external_requests = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=os.environ.get(
                "CHROMIUM_EXECUTABLE", shutil.which("chromium")
            ),
            headless=True,
            args=["--no-sandbox"],
        )
        page = await browser.new_page(
            viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Shanghai"
        )
        page.on("pageerror", lambda err: failures.append(str(err)))
        reject_discard = False
        discard_confirmations = []

        async def handle_dialog(dialog):
            if "尚未保存" in dialog.message:
                discard_confirmations.append(dialog.message)
                if reject_discard:
                    await dialog.dismiss()
                    return
            await dialog.accept()

        page.on("dialog", handle_dialog)

        async def offline_route(route):
            if urlsplit(route.request.url).netloc != urlsplit(url).netloc:
                external_requests.append(route.request.url)
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", offline_route)
        await page.goto(url)
        await expect(page.get_by_role("heading", name="还没有设备")).to_be_visible()
        await page.get_by_role("button", name="存储与恢复设置", exact=True).click()
        actual_health = await page.request.get(url + "/api/health")
        directory = (await actual_health.json())["storage"]["data_dir"]
        path_field = page.get_by_label("当前数据目录", exact=True)
        await expect(path_field).to_have_value(directory)
        assert await path_field.evaluate("field => field.readOnly")
        for width in (1440, 1024, 390):
            await page.set_viewport_size(
                {"width": width, "height": 1000 if width > 500 else 844}
            )
            await expect(path_field).to_be_visible()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            ), f"Storage dialog overflow at {width}"
            await page.screenshot(path=str(output / f"storage-width-{width}.png"))
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.keyboard.press("Escape")
        await expect(page.get_by_role("dialog")).to_have_count(0)
        await page.locator(".empty .primary").click()
        await page.get_by_label("设备名称", exact=True).fill("验收温控设备")
        await page.get_by_label("Modbus 端口", exact=True).fill("15120")
        await page.get_by_role("button", name="创建设备", exact=True).click()
        await expect(
            page.get_by_role("heading", name="验收温控设备", exact=True)
        ).to_be_visible()
        await expect(
            page.get_by_role("button", name="目标温度", exact=True)
        ).to_be_visible()
        # A drawer overlays the existing workspace without moving it or
        # removing the scrollbar's width. Only its own content can scroll.
        await page.set_viewport_size({"width": 1440, "height": 500})
        await page.evaluate("window.scrollTo(0, 150)")
        trigger = page.get_by_role("button", name="目标温度", exact=True)
        await trigger.scroll_into_view_if_needed()
        background = await page.locator("main").bounding_box()
        scroll = await page.evaluate("scrollY")
        assert scroll > 0
        await trigger.click()
        drawer = page.get_by_role("dialog")
        await expect(drawer).to_be_visible()
        bounds = await drawer.bounding_box()
        assert bounds["x"] > 0 and abs(bounds["x"] + bounds["width"] - 1440) <= 1
        assert await page.locator("main").bounding_box() == background
        assert await page.evaluate("scrollY") == scroll
        assert await page.locator("main").evaluate("main => main.inert")
        await page.mouse.move(300, 250)
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(150)
        assert await page.evaluate("scrollY") == scroll
        await page.mouse.move(1200, 350)
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(150)
        assert await page.locator(".drawer-content").evaluate("el => el.scrollTop") > 0
        assert await page.evaluate("scrollY") == scroll
        await page.screenshot(path=str(output / "drawer-background-stationary.png"))
        await page.keyboard.press("Escape")
        await expect(drawer).to_have_count(0)
        await expect(trigger).to_be_focused()
        assert await page.locator("main").bounding_box() == background
        assert await page.evaluate("scrollY") == scroll
        assert not await page.locator("main").evaluate("main => main.inert")
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_role("button", name="启动设备", exact=True).click()
        await expect(
            page.get_by_role("button", name="停止设备", exact=True)
        ).to_be_visible()
        row = page.get_by_role("row").filter(
            has=page.get_by_role("button", name="目标温度", exact=True)
        )
        await row.get_by_role("button", name="赋值", exact=True).click()
        await page.get_by_label("新工程值", exact=True).fill("75.3")
        await page.get_by_role("button", name="校验并预览编码值", exact=True).click()
        await expect(page.get_by_text("实际值 75.3", exact=False)).to_be_visible()
        await page.get_by_role("button", name="应用当前值", exact=True).click()
        await expect(row.locator(".live-value")).to_contain_text("75.30")
        await page.get_by_role("button", name="暂停策略", exact=True).click()
        await expect(
            page.get_by_role("button", name="恢复策略", exact=True)
        ).to_be_visible()
        await page.get_by_role("button", name="恢复策略", exact=True).click()
        await page.get_by_role("button", name="目标温度", exact=True).click()
        await expect(page.get_by_role("heading", name="编辑点位")).to_be_visible()
        await page.get_by_label("名称", exact=True).fill("目标温度设定")
        await page.get_by_role("button", name="保存配置", exact=True).click()
        await expect(
            page.get_by_role("button", name="目标温度设定", exact=True)
        ).to_be_visible()
        await page.get_by_role("button", name="目标温度设定", exact=True).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("倍率", exact=True).fill("0")
        await dialog.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_label("倍率", exact=True)).to_have_value("0")
        await expect(dialog.locator(".message.error")).to_contain_text("倍率")
        # Errors retain edits; Escape returns keyboard focus to the table.
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)
        await expect(
            page.get_by_role("button", name="目标温度设定", exact=True)
        ).to_be_focused()
        await page.get_by_role("button", name="实时趋势", exact=True).click()
        await expect(
            page.get_by_role("heading", name="实时趋势", exact=True)
        ).to_be_visible()
        await page.wait_for_timeout(1500)
        for width in (1440, 1024, 390):
            await page.set_viewport_size(
                {"width": width, "height": 1000 if width > 500 else 844}
            )
            await page.screenshot(
                path=str(output / f"width-{width}.png"), full_page=True
            )
            overflow = await page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            )
            assert not overflow, f"Page overflow at {width}"
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_role("button", name="停止设备", exact=True).click()
        await expect(
            page.get_by_role("button", name="启动设备", exact=True)
        ).to_be_visible()
        await page.get_by_role("button", name="新增点位", exact=True).click()
        await page.get_by_label("名称", exact=True).fill("随机测试点")
        await page.get_by_label("策略类型", exact=True).select_option("random")
        await page.get_by_role("button", name="保存配置", exact=True).click()
        await expect(
            page.get_by_role("button", name="随机测试点", exact=True)
        ).to_be_visible()
        await page.get_by_role("button", name="随机测试点", exact=True).click()
        await page.get_by_label("策略类型", exact=True).select_option("noise")
        await page.get_by_text("附加策略参数（JSON）", exact=True).click()
        await page.get_by_label("附加策略参数", exact=True).fill(
            '{"base":"fixed","value":25,"distribution":"normal"}'
        )
        await page.get_by_role("button", name="保存配置", exact=True).click()
        await expect(page.get_by_role("dialog")).to_have_count(0)
        await page.get_by_role("button", name="随机测试点", exact=True).click()
        advanced = page.get_by_label("附加策略参数", exact=True)
        await page.get_by_text("附加策略参数（JSON）", exact=True).click()
        assert json.loads(await advanced.input_value())["base"] == "fixed"
        await advanced.fill('{"base":"sine","distribution":"uniform"}')
        reject_discard = True
        before = len(discard_confirmations)
        await page.keyboard.press("Escape")
        await expect(page.get_by_role("dialog")).to_be_visible()
        assert len(discard_confirmations) == before + 1
        await expect(advanced).to_have_value('{"base":"sine","distribution":"uniform"}')
        reject_discard = False
        await page.get_by_role("button", name="保存配置", exact=True).click()
        await expect(page.get_by_role("dialog")).to_have_count(0)
        saved = await (await page.request.get(url + "/api/config")).json()
        params = next(
            p["strategy"]["params"]
            for p in saved["devices"][0]["points"]
            if p["name"] == "随机测试点"
        )
        assert params["base"] == "sine" and params["distribution"] == "uniform"
        assert "value" not in params and params["mean"] == 50
        # Slow management responses must not accumulate interval requests.
        pending, maximum = 0, 0

        async def slow_devices(route):
            nonlocal pending, maximum
            pending += 1
            maximum = max(maximum, pending)
            try:
                await asyncio.sleep(2.5)
                await route.continue_()
            finally:
                pending -= 1

        await page.route("**/api/devices", slow_devices)
        await page.wait_for_timeout(6500)
        await page.unroute("**/api/devices", slow_devices)
        assert maximum == 1, f"Overlapping device polls: {maximum}"
        await page.get_by_role("button", name="通信诊断", exact=True).click()
        await expect(
            page.get_by_role("heading", name="通信诊断", exact=True)
        ).to_be_visible()
        font = await page.evaluate(
            "getComputedStyle(document.querySelector('h1')).fontFamily"
        )
        assert "IBM Plex Sans" in font
        assert await page.evaluate("document.fonts.check('16px \"IBM Plex Sans\"')")
        assert not failures, failures
        assert not external_requests, external_requests
        # The same app serves both deep links and static frontend files.
        await page.goto(url + "/devices/overview")
        await expect(
            page.get_by_role("heading", name="验收温控设备", exact=True)
        ).to_be_visible()
        # Both creation modes and editing must reject the same endpoint/unit
        # without changing persisted state or discarding the user's form.
        before = await (await page.request.get(url + "/api/config")).json()
        for mode in ("温控模板（4 个联动点位）", "空设备"):
            await page.get_by_role("button", name="新建设备", exact=True).click()
            dialog = page.get_by_role("dialog")
            await dialog.get_by_label("设备名称", exact=True).fill("重复设备")
            await dialog.get_by_label("监听 IP", exact=True).fill("127.0.0.1")
            await dialog.get_by_label("初始配置", exact=True).select_option(label=mode)
            await dialog.get_by_label("Modbus 端口", exact=True).fill("15120")
            await dialog.get_by_label("Unit ID", exact=True).fill("1")
            await dialog.get_by_role("button", name="创建设备", exact=True).click()
            await expect(dialog.locator(".message.error")).to_contain_text(
                "同一端点 Unit ID 重复"
            )
            await expect(dialog.locator(".message.error")).to_contain_text(
                "验收温控设备"
            )
            await expect(dialog.get_by_label("设备名称", exact=True)).to_have_value(
                "重复设备"
            )
            assert await (await page.request.get(url + "/api/config")).json() == before
            await dialog.get_by_label("监听 IP", exact=True).fill("0.0.0.0")
            for unit in ("1", "2"):
                await dialog.get_by_label("Unit ID", exact=True).fill(unit)
                await dialog.get_by_role("button", name="创建设备", exact=True).click()
                await expect(dialog.locator(".message.error")).to_contain_text(
                    "监听地址冲突"
                )
                await expect(dialog.get_by_label("监听 IP", exact=True)).to_have_value(
                    "0.0.0.0"
                )
                assert (
                    await (await page.request.get(url + "/api/config")).json() == before
                )
            await page.keyboard.press("Escape")
        await page.get_by_role("button", name="新建设备", exact=True).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("设备名称", exact=True).fill("共享端点第二台")
        await dialog.get_by_label("监听 IP", exact=True).fill("127.0.0.1")
        await dialog.get_by_label("Unit ID", exact=True).fill("2")
        await dialog.get_by_role("button", name="创建设备", exact=True).click()
        await expect(
            page.get_by_role("heading", name="共享端点第二台", exact=True)
        ).to_be_visible()
        await (
            page.locator(".page-header")
            .get_by_role("button", name="设备设置", exact=True)
            .click()
        )
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("Unit ID", exact=True).fill("1")
        before = await (await page.request.get(url + "/api/config")).json()
        await dialog.get_by_role("button", name="保存设备配置", exact=True).click()
        await expect(dialog.locator(".message.error")).to_contain_text(
            "同一端点 Unit ID 重复"
        )
        await expect(dialog.get_by_label("Unit ID", exact=True)).to_have_value("1")
        assert await (await page.request.get(url + "/api/config")).json() == before
        await dialog.get_by_label("Unit ID", exact=True).fill("2")
        await dialog.get_by_label("绑定 IP", exact=True).fill("0.0.0.0")
        await dialog.get_by_role("button", name="保存设备配置", exact=True).click()
        await expect(dialog.locator(".message.error")).to_contain_text("监听地址冲突")
        assert await (await page.request.get(url + "/api/config")).json() == before
        await dialog.get_by_label("绑定 IP", exact=True).fill("127.0.0.1")
        await dialog.get_by_role("button", name="保存设备配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        assert not failures, failures
        await browser.close()
    report = {
        "passed": True,
        "checks": [
            "actual read-only storage directory at 1440/1024/390 widths",
            "right drawer preserves workspace width and scroll, locks background and restores focus",
            "device creation",
            "template and empty creation reject duplicate endpoint/unit and retain drafts",
            "distinct unit shares endpoint; editing rejects duplicates and excludes itself",
            "wildcard/specific address overlap rejected in both creation modes and editing",
            "start/stop",
            "assignment preview",
            "assignment confirmation",
            "pause/resume",
            "point edit",
            "point creation",
            "advanced strategy edit and parameter deletion persist",
            "advanced-only draft requires discard confirmation",
            "slow polling has at most one request",
            "invalid edit retains draft",
            "keyboard Escape and focus return",
            "real-time trend",
            "diagnostics",
            "1440/1024/390 layout",
            "local font",
            "external network blocked",
            "SPA deep link",
        ],
        "page_errors": failures,
    }
    (output / "browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--output", default="artifacts/browser")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
