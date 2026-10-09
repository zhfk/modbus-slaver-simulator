"""Real Chromium functional and responsive checks against the backend."""

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
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
        device_confirmations = []
        reject_delete = False

        async def handle_dialog(dialog):
            if any(
                word in dialog.message for word in ("停止 ", "删除 ", "的全部点位恢复")
            ):
                device_confirmations.append(dialog.message)
                if reject_delete and dialog.message.startswith("删除 "):
                    await dialog.dismiss()
                    return
            if "尚未保存" in dialog.message:
                discard_confirmations.append(dialog.message)
                if reject_discard:
                    await dialog.dismiss()
                    return
            await dialog.accept()

        page.on("dialog", handle_dialog)

        async def open_device_menu(name):
            card = page.locator(".device-link").filter(has_text=name)
            if not await card.is_visible():
                await page.get_by_role(
                    "button", name="展开设备导航", exact=True
                ).click()
            await card.click(button="right")
            menu = page.get_by_role("menu", name="设备操作", exact=True)
            await expect(menu).to_be_visible()
            return menu

        async def device_action(name, command):
            menu = await open_device_menu(name)
            await menu.get_by_role("menuitem", name=command, exact=True).click()
            await expect(menu).to_be_hidden()

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
        for width in (1440, 1024, 390):
            await page.set_viewport_size({"width": width, "height": 844})
            row = page.get_by_role("row").filter(
                has=page.get_by_role("button", name="目标温度", exact=True)
            )
            more = row.get_by_role("button", name="更多点位操作", exact=True)
            await more.scroll_into_view_if_needed()
            before_height = (await row.bounding_box())["height"]
            await more.click()
            menu = page.locator(".point-menu:popover-open")
            await expect(menu).to_be_visible()
            await page.wait_for_timeout(100)
            assert (await row.bounding_box())["height"] == before_height
            bounds = await menu.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 844
            await page.screenshot(path=str(output / f"row-menu-{width}.png"))
            await page.keyboard.press("Escape")
            await expect(menu).to_have_count(0)
            await expect(more).to_be_focused()
            await more.click()
            await page.get_by_role("heading", name="验收温控设备", exact=True).click()
            await expect(menu).to_have_count(0)
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await more.click()
        await (
            page.locator(".point-menu:popover-open")
            .get_by_role("button", name="查看 / 编辑", exact=True)
            .click()
        )
        await expect(page.get_by_role("dialog")).to_be_visible()
        await expect(page.locator(".point-menu:popover-open")).to_have_count(0)
        await page.keyboard.press("Escape")
        await expect(more).to_be_focused()
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
        await device_action("验收温控设备", "启动设备")
        await expect(
            page.locator(".device-link").filter(has_text="验收温控设备")
        ).to_contain_text("运行中")
        row = page.get_by_role("row").filter(
            has=page.get_by_role("button", name="目标温度", exact=True)
        )
        await row.get_by_role("button", name="赋值", exact=True).click()
        await page.get_by_label("新工程值", exact=True).fill("75.3")
        await page.get_by_role("button", name="校验并预览编码值", exact=True).click()
        await expect(page.get_by_text("实际值 75.3", exact=False)).to_be_visible()
        await page.get_by_role("button", name="应用当前值", exact=True).click()
        await expect(row.locator(".live-value")).to_contain_text("75.30")
        await device_action("验收温控设备", "暂停策略")
        await expect(
            page.locator(".device-link").filter(has_text="验收温控设备")
        ).to_contain_text("策略暂停")
        await device_action("验收温控设备", "恢复策略")
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
        trend_queries = []
        page.on(
            "request",
            lambda req: (
                trend_queries.append(parse_qs(urlsplit(req.url).query).get("ids", []))
                if "/trends?" in req.url
                else None
            ),
        )
        await page.get_by_label("选择当前页全部点位", exact=True).check()
        for width in (1440, 1024, 390):
            await page.set_viewport_size(
                {"width": width, "height": 1000 if width > 500 else 844}
            )
            trigger = page.get_by_role(
                "button", name="查看 目标温度设定 的实时趋势", exact=True
            )
            await trigger.scroll_into_view_if_needed()
            height = (await page.locator(".monitor-panel").bounding_box())["height"]
            await trigger.click()
            popup = page.locator("#point-trend-popover:popover-open")
            await expect(popup).to_be_visible()
            await expect(popup.locator(".chart")).to_have_count(1)
            await expect(popup.locator(".chart strong")).to_have_text("目标温度设定")
            assert (await page.locator(".monitor-panel").bounding_box())[
                "height"
            ] == height
            await page.wait_for_timeout(1100)
            bounds = await popup.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= (
                1000 if width > 500 else 844
            )
            await page.screenshot(path=str(output / f"trend-popover-{width}.png"))
            await page.keyboard.press("Escape")
            await expect(popup).to_have_count(0)
            await expect(trigger).to_be_focused()
            await page.screenshot(
                path=str(output / f"width-{width}.png"), full_page=True
            )
            overflow = await page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            )
            assert not overflow, f"Page overflow at {width}"
        assert trend_queries and all(
            len(ids) == 1 and "," not in ids[0] for ids in trend_queries
        )
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_label("选择当前页全部点位", exact=True).uncheck()
        await device_action("验收温控设备", "停止设备")
        await expect(
            page.locator(".device-link").filter(has_text="验收温控设备")
        ).to_contain_text("已停止")
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
        await device_action("共享端点第二台", "编辑设备")
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
        await page.locator(".device-link").filter(has_text="验收温控设备").click()
        await page.get_by_role("button", name="目标温度设定", exact=True).click()
        dialog = page.get_by_role("dialog")
        strategy = dialog.get_by_label("策略类型", exact=True)
        await expect(strategy).to_be_enabled()
        await expect(dialog.get_by_label("写入后行为", exact=True)).to_have_value(
            "control"
        )
        await strategy.select_option("sine")
        await expect(dialog.get_by_label("写入后行为", exact=True)).to_have_value(
            "hold"
        )
        await dialog.get_by_label("更新周期（秒）", exact=True).fill("0.1")
        await dialog.get_by_label("幅度", exact=True).fill("5")
        await dialog.get_by_label("周期（秒）", exact=True).fill("4")
        await dialog.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        await device_action("验收温控设备", "启动设备")
        await page.wait_for_timeout(500)
        points = await (
            await page.request.get(
                url
                + "/api/devices/"
                + (await (await page.request.get(url + "/api/config")).json())[
                    "devices"
                ][0]["id"]
                + "/points"
            )
        ).json()
        target = next(p for p in points["items"] if p["name"] == "目标温度设定")
        assert target["strategy"]["kind"] == "sine" and target["state"] == "running"
        assert 45 <= target["value"] <= 55
        await page.get_by_role("button", name="目标温度设定", exact=True).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_text("附加策略参数（JSON）", exact=True).click()
        await dialog.get_by_label("附加策略参数", exact=True).fill(
            '{"base":"fixed","value":25}'
        )
        await dialog.get_by_label("写入后行为", exact=True).select_option("control")
        await expect(dialog.get_by_label("策略类型", exact=True)).to_have_value("none")
        await expect(dialog.get_by_label("策略类型", exact=True)).to_be_enabled()
        await expect(dialog.get_by_label("附加策略参数", exact=True)).to_have_value(
            "{}"
        )
        await dialog.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        for kind in ("sequence", "replay"):
            await page.get_by_role("button", name="目标温度设定", exact=True).click()
            dialog = page.get_by_role("dialog")
            await dialog.get_by_label("策略类型", exact=True).select_option(kind)
            await dialog.get_by_role("button", name="保存配置", exact=True).click()
            await expect(dialog).to_have_count(0)
        await page.get_by_role("button", name="目标温度设定", exact=True).click()
        await page.get_by_label("写入后行为", exact=True).select_option("control")
        await page.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        await page.get_by_role("button", name="启动命令", exact=True).click()
        dialog = page.get_by_role("dialog")
        strategy = dialog.get_by_label("策略类型", exact=True)
        await expect(strategy).to_be_enabled()
        assert await strategy.locator("option[value='random']").count() == 0
        assert await strategy.locator("option[value='sine']").count() == 0
        await strategy.select_option("fixed")
        await dialog.get_by_label("固定值", exact=True).select_option("1")
        await dialog.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        await page.get_by_role("button", name="启动命令", exact=True).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("策略类型", exact=True).select_option("sequence")
        await dialog.get_by_label("更新周期（秒）", exact=True).fill("0.1")
        samples = dialog.get_by_role("textbox", name="样本", exact=False)
        await samples.fill("[[0.3,0],[0.3,2]]")
        before = await (await page.request.get(url + "/api/config")).json()
        await dialog.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog.locator(".message.error")).to_contain_text(
            "样本值只能为 0／1"
        )
        assert await (await page.request.get(url + "/api/config")).json() == before
        await samples.fill("[[0.3,0],[0.3,1]]")
        await dialog.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        key = before["devices"][0]["id"]
        seen = set()
        for _ in range(12):
            response = await (
                await page.request.get(url + f"/api/devices/{key}/points")
            ).json()
            command = next(p for p in response["items"] if p["name"] == "启动命令")
            assert isinstance(command["value"], bool) and not command["error"]
            seen.add(command["value"])
            if len(seen) == 2:
                break
            await page.wait_for_timeout(150)
        assert seen == {False, True}, seen
        await page.get_by_role("button", name="启动命令", exact=True).click()
        await page.get_by_label("写入后行为", exact=True).select_option("control")
        await page.get_by_role("button", name="保存配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        # Device actions always target the card, even when another device is
        # selected. A concrete point and history reference exercise reset/delete.
        candidate = await (await page.request.get(url + "/api/config")).json()
        second = candidate["devices"][1]
        second["points"] = [
            {"id": "context-reset-point", "name": "重置验证", "initial": 12}
        ]
        candidate["settings"]["history_points"].append("context-reset-point")
        response = await page.request.put(url + "/api/config", data=candidate)
        assert response.status == 200, await response.text()
        await page.reload()
        await expect(
            page.get_by_role("heading", name="验收温控设备", exact=True)
        ).to_be_visible()
        start_gate = asyncio.Event()

        async def delayed_start(route):
            await start_gate.wait()
            await route.continue_()

        start_url = url + f"/api/devices/{second['id']}/actions/start"
        await page.route(start_url, delayed_start)
        try:
            await device_action("共享端点第二台", "启动设备")
            menu = await open_device_menu("共享端点第二台")
            await expect(menu.locator("button:not(:disabled)")).to_have_count(0)
            await page.keyboard.press("Escape")
            await expect(menu).to_be_hidden()
        finally:
            start_gate.set()
        await expect(
            page.locator(".device-link").filter(has_text="共享端点第二台")
        ).to_contain_text("运行中")
        await page.unroute(start_url, delayed_start)
        await expect(
            page.get_by_role("heading", name="验收温控设备", exact=True)
        ).to_be_visible()
        menu = await open_device_menu("共享端点第二台")
        await expect(
            menu.get_by_role("menuitem", name="启动设备", exact=True)
        ).to_be_disabled()
        await expect(
            menu.get_by_role("menuitem", name="重置全部当前值", exact=True)
        ).to_be_disabled()
        await expect(
            menu.get_by_role("menuitem", name="删除设备", exact=True)
        ).to_be_disabled()
        await page.keyboard.press("Escape")
        await device_action("共享端点第二台", "停止设备")
        await expect(
            page.locator(".device-link").filter(has_text="共享端点第二台")
        ).to_contain_text("已停止")
        response = await page.request.post(
            url + f"/api/devices/{second['id']}/assign",
            data={"items": [{"id": "context-reset-point", "value": 99}]},
        )
        assert response.status == 200
        menu = await open_device_menu("共享端点第二台")
        async with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/devices/{second['id']}/actions/reset")
                and response.request.method == "POST"
            )
        ) as reset_response:
            await menu.get_by_role(
                "menuitem", name="重置全部当前值", exact=True
            ).click()
        assert (await reset_response.value).status == 200
        await expect(page.get_by_role("status")).to_contain_text("共享端点第二台")
        value = await (
            await page.request.get(url + f"/api/devices/{second['id']}/points")
        ).json()
        assert value["items"][0]["value"] == 12
        await device_action("共享端点第二台", "设备设置")
        await expect(
            page.get_by_role("heading", name="设备配置", exact=True)
        ).to_be_visible()
        assert (
            await page.locator(".page-header button")
            .filter(has_text="启动设备")
            .count()
            == 0
        )
        assert await page.locator(".settings-summary button").count() == 0
        await device_action("共享端点第二台", "编辑设备")
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("设备名称", exact=True).fill("菜单设备 2#")
        await dialog.get_by_role("button", name="保存设备配置", exact=True).click()
        await expect(dialog).to_have_count(0)
        for width in (1440, 1024, 390):
            await page.set_viewport_size({"width": width, "height": 844})
            card = page.locator(".device-link").filter(has_text="菜单设备 2#")
            if not await card.is_visible():
                await page.get_by_role(
                    "button", name="展开设备导航", exact=True
                ).click()
            await page.get_by_role(
                "button", name="菜单设备 2# 的设备操作菜单", exact=True
            ).click()
            menu = page.get_by_role("menu", name="设备操作", exact=True)
            await expect(menu).to_be_visible()
            bounds = await menu.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 844
            await page.screenshot(path=str(output / f"device-menu-{width}.png"))
            await page.keyboard.press("End")
            await expect(
                menu.get_by_role("menuitem", name="删除设备", exact=True)
            ).to_be_focused()
            await page.keyboard.press("Home")
            await expect(
                menu.get_by_role("menuitem", name="设备设置", exact=True)
            ).to_be_focused()
            await page.keyboard.press("ArrowDown")
            assert (
                await page.evaluate("document.activeElement.getAttribute('role')")
                == "menuitem"
            )
            await page.keyboard.press("Escape")
            await expect(card).to_be_focused()
            await card.press("Shift+F10")
            await expect(menu).to_be_visible()
            await page.keyboard.press("ArrowUp")
            await expect(
                menu.get_by_role("menuitem", name="删除设备", exact=True)
            ).to_be_focused()
            await page.keyboard.press("Escape")
        await page.set_viewport_size({"width": 1440, "height": 1000})
        card = page.locator(".device-link").filter(has_text="菜单设备 2#")
        before = await card.bounding_box()
        scroll_before = await page.evaluate("scrollY")
        menu = await open_device_menu("菜单设备 2#")
        assert await card.bounding_box() == before
        assert await page.evaluate("scrollY") == scroll_before
        await page.get_by_role("heading", name="菜单设备 2#", exact=True).click()
        await expect(menu).to_be_hidden()
        await open_device_menu("菜单设备 2#")
        await page.set_viewport_size({"width": 1430, "height": 1000})
        await expect(menu).to_be_hidden()
        await page.set_viewport_size({"width": 1430, "height": 500})
        await open_device_menu("菜单设备 2#")
        scroll_before = await page.evaluate("scrollY")
        await page.evaluate("window.scrollBy(0, 30)")
        await expect(menu).to_be_hidden()
        assert await page.evaluate("scrollY") > scroll_before
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.locator(".device-link").filter(has_text="验收温控设备").click()
        reject_delete = True
        await device_action("菜单设备 2#", "删除设备")
        assert "菜单设备 2#" in device_confirmations[-1]
        assert len((await (await page.request.get(url + "/api/devices")).json())) == 2
        reject_delete = False
        await device_action("菜单设备 2#", "删除设备")
        await expect(
            page.locator(".device-link").filter(has_text="菜单设备 2#")
        ).to_have_count(0)
        saved = await (await page.request.get(url + "/api/config")).json()
        assert len(saved["devices"]) == 1 and saved["settings"]["history_points"] == []
        devices = await (await page.request.get(url + "/api/devices")).json()
        assert (
            devices[0]["name"] == "验收温控设备" and devices[0]["status"] == "running"
        )
        assert not failures, failures
        await browser.close()
    report = {
        "passed": True,
        "checks": [
            "actual read-only storage directory at 1440/1024/390 widths",
            "right drawer preserves workspace width and scroll, locks background and restores focus",
            "device creation",
            "device context menu controls explicit card target without changing another selected device",
            "device context menu settings/edit/start/stop/reset/delete, state guards and cancelled deletion",
            "device menu at 1440/1024/390: trigger/Shift+F10, arrows/Home/End/Escape, unchanged layout, outside/scroll/resize dismissal",
            "deleting an inactive device clears point/history references and preserves the running device",
            "row menu overlays without changing row height at 1440/1024/390, stays in viewport, dismisses and restores focus",
            "control input can select and save a generated strategy, then runs it",
            "returning to control input clears strategy and extra parameters",
            "sequence and replay switches initialize valid samples",
            "Bool strategy choices and on/off fixed values, invalid sample retention, actual 0/1 sequence updates",
            "single-point trend popover at 1440/1024/390 preserves list height and ignores bulk selection",
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
