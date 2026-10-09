"""Actual assignment history and help/tour checks, shared by browser_check."""

import asyncio
import struct
import re
import socket

from playwright.async_api import expect
from simulator.models import Device


async def check_help_and_history(page, url, output):
    checks = []
    current = await (await page.request.get(url + "/api/config")).json()
    # Earlier suites intentionally import/delete/remap points. Give this
    # scenario its own real devices rather than assuming their final layout.
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    created = await page.request.post(
        url + "/api/templates/thermal",
        data={"version": current["version"], "name": "赋值历史验收", "port": port},
    )
    assert created.status == 200, await created.text()
    key = (await created.json())["id"]
    current = await (await page.request.get(url + "/api/config")).json()
    first = next(d for d in current["devices"] if d["id"] == key)
    second = Device(
        id="history-other", name="历史隔离设备", port=port, unit_id=2
    ).model_dump()
    current["devices"].append(second)
    saved = await page.request.put(url + "/api/config", data=current)
    assert saved.status == 200, await saved.text()
    point = next(p for p in first["points"] if p["name"] == "目标温度")
    key = first["id"]
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.reload()
    await page.locator(".device-link").filter(has_text=first["name"]).click()
    await page.get_by_role("button", name="点位监控", exact=True).click()
    row = page.get_by_role("row").filter(
        has=page.get_by_role("button", name=point["name"], exact=True)
    )
    await row.get_by_role("button", name="赋值", exact=True).click()
    await page.get_by_label("新工程值", exact=True).fill("71.25")
    await page.get_by_role("button", name="应用当前值", exact=True).click()
    await expect(page.locator(".drawer")).to_be_hidden()
    response = await page.request.post(
        url + f"/api/devices/{key}/assign",
        data={"items": [{"id": point["id"], "value": 72.25}]},
    )
    assert response.status == 200
    response = await page.request.post(
        url + f"/api/devices/{key}/assign",
        data={"items": [{"id": point["id"], "value": "invalid"}]},
    )
    assert response.status == 422
    assert (
        await page.request.post(url + f"/api/devices/{key}/actions/start")
    ).status == 200
    reader, writer = await asyncio.open_connection(first["host"], first["port"])
    source_port = writer.get_extra_info("sockname")[1]
    try:

        async def write(pdu):
            writer.write(
                struct.pack(">HHHB", 909, 0, len(pdu) + 1, first["unit_id"]) + pdu
            )
            await writer.drain()
            header = await asyncio.wait_for(reader.readexactly(7), 2)
            return await asyncio.wait_for(
                reader.readexactly(int.from_bytes(header[4:6], "big") - 1), 2
            )

        pdu = struct.pack(">BHHB", 16, point["address"], 2, 4) + struct.pack(
            ">f", 73.25
        )
        assert (await write(pdu))[0] == 16
        assert (await write(bytes.fromhex("06ffff0001")))[0] == 0x86
    finally:
        writer.close()
        await writer.wait_closed()
        assert (
            await page.request.post(url + f"/api/devices/{key}/actions/stop")
        ).status == 200
    await page.get_by_role("button", name="赋值历史", exact=True).click()
    panel = page.locator(".assignment-panel")
    await expect(panel).to_contain_text("页面赋值")
    await expect(panel).to_contain_text("API 赋值")
    await expect(panel).to_contain_text(f"127.0.0.1:{source_port}")
    await expect(panel.locator("tbody tr").first).to_contain_text("失败")
    await expect(panel.locator("tbody tr").nth(1)).to_contain_text("Modbus 写入")
    await expect(panel.locator("tbody tr").nth(1)).to_contain_text("73.25")
    timestamp = await panel.locator("tbody tr").first.locator("td").first.inner_text()
    assert re.search(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", timestamp), (
        timestamp
    )
    checks.append(
        "actual UI/API/Modbus successful and failed assignments, distinct sources/host/function, descending timestamps"
    )
    for width in (1440, 1024, 390):
        await page.set_viewport_size(
            {"width": width, "height": 1000 if width > 500 else 844}
        )
        await expect(panel).to_be_visible()
        assert not await page.evaluate(
            "document.documentElement.scrollWidth > innerWidth"
        )
        await page.screenshot(path=str(output / f"assignment-history-{width}.png"))
        # Detail should retain an immutable snapshot while polling updates rows.
        trigger = (
            panel.locator("tbody tr")
            .nth(1)
            .get_by_role("button", name="查看", exact=False)
        )
        await trigger.click()
        dialog = page.get_by_role("dialog", name="赋值记录详情", exact=True)
        await expect(dialog).to_contain_text("73.25")
        await expect(dialog).to_contain_text(f"127.0.0.1:{source_port}")
        snapshot = await dialog.locator(".assignment-detail").inner_text()
        await page.wait_for_timeout(1100)
        assert await dialog.locator(".assignment-detail").inner_text() == snapshot
        await page.screenshot(path=str(output / f"assignment-detail-{width}.png"))
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(trigger).to_be_focused()
    checks.append(
        "assignment history/detail at 1440/1024/390 without page overflow, stable snapshot and Escape focus return"
    )
    await page.set_viewport_size({"width": 1440, "height": 1000})
    trigger = (
        panel.locator("tbody tr").nth(1).get_by_role("button", name="查看", exact=False)
    )
    await trigger.click()
    detail = page.locator(".assignment-detail")
    snapshot = await detail.inner_text()
    for value in range(105):
        assert (
            await page.request.post(
                url + f"/api/devices/{key}/assign",
                data={"items": [{"id": point["id"], "value": value}]},
            )
        ).status == 200
    await expect(panel.locator("tbody tr")).to_have_count(100)
    assert await detail.inner_text() == snapshot
    await page.keyboard.press("Escape")
    await expect(page.locator("#assignments-tab")).to_be_focused()
    await page.locator(".device-link").filter(has_text=second["name"]).click()
    await expect(panel.locator("tbody tr")).to_have_count(0)
    await page.locator(".device-link").filter(has_text=first["name"]).click()
    await expect(panel.locator("tbody tr")).to_have_count(100)
    checks.append(
        "100-record cap, isolated per-device history, evicted record detail preserved with valid fallback focus"
    )

    before = await (await page.request.get(url + "/api/config")).json()
    await page.get_by_role("button", name="使用帮助", exact=True).click()
    await expect(
        page.get_by_role("heading", name="使用帮助", exact=True)
    ).to_be_visible()
    assert await page.locator(".help-topic").count() >= 18
    await expect(page).to_have_url(url + "/help")
    for width in (1440, 1024, 390):
        await page.set_viewport_size(
            {"width": width, "height": 1000 if width > 500 else 844}
        )
        await page.get_by_label("搜索帮助", exact=True).fill("赋值历史")
        await expect(page.locator("#help-assignment-history")).to_be_visible()
        assert not await page.evaluate(
            "document.documentElement.scrollWidth > innerWidth"
        )
        await page.screenshot(path=str(output / f"help-{width}.png"))
        await page.get_by_label("搜索帮助", exact=True).fill("does-not-exist-xxxxx")
        await expect(page.get_by_role("status")).to_contain_text("未找到匹配内容")
    await page.get_by_label("搜索帮助", exact=True).fill("")
    await page.reload()
    await expect(
        page.get_by_role("heading", name="使用帮助", exact=True)
    ).to_be_visible()
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.get_by_role(
        "link", name="9. 表达式、依赖代号与有效时间", exact=True
    ).click()
    await expect(page).to_have_url(url + "/help#help-expression")
    await expect(page.locator("#help-expression")).to_be_in_viewport()
    await page.get_by_role("button", name="返回工作区", exact=True).click()
    await expect(page.locator(".help-view")).to_have_count(0)
    await page.go_back()
    await expect(
        page.get_by_role("heading", name="使用帮助", exact=True)
    ).to_be_visible()
    checks.append(
        "searchable full help, empty search state, direct /help refresh, section anchors and browser back at three widths"
    )
    await page.get_by_role("button", name="开始使用引导", exact=True).click()
    tour = page.get_by_role("dialog", name="使用引导", exact=True)
    await expect(tour).to_contain_text("1／9")
    await expect(tour.get_by_role("button", name="上一步", exact=True)).to_be_disabled()
    await tour.get_by_role("button", name="打开新建设备", exact=True).click()
    await expect(tour).to_have_count(0)
    await expect(
        page.get_by_role("dialog", name="新建设备", exact=True)
    ).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(tour).to_contain_text("1／9")
    for index in range(1, 9):
        await tour.get_by_role("button", name="下一步", exact=True).click()
        await expect(tour).to_contain_text(f"{index + 1}／9")
    await tour.get_by_role("button", name="上一步", exact=True).click()
    await expect(tour).to_contain_text("8／9")
    await tour.get_by_role("button", name="下一步", exact=True).click()
    await tour.get_by_role("button", name="完成引导", exact=True).click()
    await expect(tour).to_have_count(0)
    for width in (1440, 1024, 390):
        await page.set_viewport_size(
            {"width": width, "height": 1000 if width > 500 else 844}
        )
        await page.get_by_role("button", name="使用引导", exact=True).click()
        await expect(tour).to_be_visible()
        bounds = await tour.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert bounds["y"] >= 0
        await page.screenshot(path=str(output / f"guide-{width}.png"))
        await page.keyboard.press("Escape")
        await expect(tour).to_have_count(0)
    assert await (await page.request.get(url + "/api/config")).json() == before
    assert await page.locator(".tour-target").count() == 0
    checks.append(
        "nine-step interactive guide, draft action/return, previous/next/finish/Escape, responsive bounds and no automatic config changes"
    )
    return checks
