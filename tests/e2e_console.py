"""Сквозная проверка HTML-консоли в браузере (Playwright): ээси → демо-маалымат → 3 түзмөк → заказ → ашкана → касса.

Запуск: сервер на :8000 с чистой базой и bootstrap (owner@dostuk.kg / owner-pass-123),
  pip install playwright; CHROMIUM_PATH=/path/to/chrome python tests/e2e_console.py
"""
import asyncio, os, re
from playwright.async_api import async_playwright, expect
B = "http://localhost:8000/app/"
SH = "./e2e-shots/"
os.makedirs(SH, exist_ok=True)
errors = []

async def new_page(ctx, name):
    p = await ctx.new_page()
    p.on("console", lambda m: m.type == "error" and errors.append(f"{name}: {m.text}"))
    p.on("pageerror", lambda e: errors.append(f"{name} pageerror: {e}"))
    if name == "cashier":
        p.on("request", lambda r: "/payments" in r.url and print("REQ", r.method, r.post_data))
        p.on("response", lambda r: "/payments" in r.url and print("RESP", r.status))
    await p.goto(B)
    return p

async def toast(p, text):
    try:
        await expect(p.locator(".toast", has_text=text).first).to_be_visible(timeout=8000)
    except AssertionError:
        print("TOASTS NOW:", await p.locator(".toast").all_inner_texts())
        print("pm/pa/pt:", await p.locator("#pm").input_value(), await p.locator("#pa").input_value(), await p.locator("#pt").input_value())
        raise

async def pairing_code(owner, name, kind):
    await owner.get_by_role("button", name="Персонал").click()
    await owner.fill("#d_name", name)
    await owner.select_option("#d_kind", kind)
    await owner.get_by_role("button", name="Создать код привязки").click()
    code = (await owner.locator("#dlg[open] .mono").inner_text()).strip()
    await owner.locator("#dlg").get_by_role("button", name="Закрыть").click()
    return code

async def device_login(p, code, staff, pin):
    await p.fill("#pair", code)
    await p.get_by_role("button", name="Привязать устройство").click()
    try:
        await p.get_by_role("button", name=staff).click(timeout=8000)
    except Exception:
        await p.screenshot(path=SH + "fail_login.png"); print("TOASTS:", await p.locator(".toast").all_inner_texts(), await p.locator("main").inner_text()); raise
    await p.keyboard.type(pin)
    await p.keyboard.press("Enter")
    await toast(p, "Здравствуйте")

async def main():
    async with async_playwright() as pw:
        br = await pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        ctx = await br.new_context(viewport={"width": 1280, "height": 860})
        owner = await new_page(ctx, "owner")
        await owner.screenshot(path=SH + "01_login.png")
        await owner.fill("#login", "owner@dostuk.kg"); await owner.fill("#password", "owner-pass-123")
        await owner.get_by_role("button", name="Войти").click()
        await toast(owner, "Здравствуйте")
        await owner.get_by_role("button", name="Инструменты").click()
        await owner.get_by_role("button", name="Заполнить демо-данными").click()
        await toast(owner, "Демо-данные созданы")
        codes = {k: await pairing_code(owner, n, k) for k, n in [("waiter", "Телефон Айбека"), ("kitchen", "Планшет кухни"), ("pos", "Касса ПК")]}
        print("codes", codes)

        waiter = await new_page(ctx, "waiter"); await device_login(waiter, codes["waiter"], "Официант Айбек", "1111")
        kitchen = await new_page(ctx, "kitchen"); await device_login(kitchen, codes["kitchen"], "Повар Нурлан", "2222")
        cashier = await new_page(ctx, "cashier"); await device_login(cashier, codes["pos"], "Кассир Айгуль", "3333")

        # Официант: стол 1 → заказ
        await waiter.locator(".tbl", has_text=re.compile(r"^1")).first.click()
        await waiter.get_by_role("button", name="Открыть заказ").click()
        await toast(waiter, "Заказ №1 открыт")
        await waiter.locator(".mi", has_text="Плов").click()
        await waiter.locator("#dlg label", has_text="Яйцо").locator("input").check()
        await waiter.locator("#dlg").get_by_role("button", name="Добавить").click()
        await waiter.locator(".mi", has_text="Бешбармак").click()
        await waiter.get_by_role("button", name="Шашлык").click()
        await waiter.locator(".mi", has_text="баранины").click()
        await waiter.locator("#dlg").get_by_role("button", name="Добавить").click()
        await waiter.get_by_role("button", name=re.compile("Отправить на кухню")).click()
        await toast(waiter, "Отправлено на кухню")
        await waiter.screenshot(path=SH + "02_waiter.png")

        # Кухня: позиции пришли сами (WebSocket)
        await expect(kitchen.locator(".ticket", has_text="№1")).to_be_visible(timeout=8000)
        await kitchen.screenshot(path=SH + "03_kitchen.png")
        await kitchen.locator(".it", has_text="Плов").get_by_role("button", name="Готовлю").click()
        await expect(kitchen.locator(".it.cooking", has_text="Плов")).to_be_visible()
        await kitchen.locator(".it", has_text="Плов").get_by_role("button", name="Готово").click()
        await toast(waiter, "Готово: Плов")   # личное событие официанту
        await waiter.locator("tr", has_text="Плов").get_by_role("button", name="Отдал").click()
        await expect(waiter.locator("tr", has_text="Плов").locator(".chip", has_text="отдано")).to_be_visible()

        # Касса: смена, разделённая оплата
        await cashier.get_by_role("button", name="Касса", exact=True).click()
        await cashier.fill("#open_cash", "1000")
        await cashier.get_by_role("button", name="Открыть смену").click()
        await toast(cashier, "Смена открыта")
        await cashier.get_by_role("button", name="Выбрать").first.click()
        total = await cashier.locator("#pa").input_value()
        print("total", total)
        await cashier.fill("#pa", "500"); await cashier.fill("#pt", "1000")
        await cashier.get_by_role("button", name="Принять оплату").click()
        await toast(cashier, "Принято, остаток")
        await cashier.select_option("#pm", "card")
        await cashier.get_by_role("button", name="Принять оплату").click()
        await toast(cashier, "Заказ оплачен")
        await expect(cashier.locator(".receipt").first).to_be_visible()
        receipt = await cashier.locator(".receipt", has_text="ЧЕК").inner_text()
        assert "cash" in receipt and "card" in receipt, receipt
        print("receipt OK: cash + card")
        assert "null" not in await waiter.locator("#view").inner_text()
        await cashier.screenshot(path=SH + "04_cashier.png", full_page=True)
        n = await cashier.get_by_role("button", name="Напечатано").count()
        print("print jobs", n)
        await cashier.get_by_role("button", name="Напечатано").first.click()

        # Стол освободился у официанта
        await expect(waiter.locator(".tbl.busy")).to_have_count(0, timeout=8000)

        # Владелец: станции, отчёты, события, сайт-меню
        await owner.get_by_role("button", name="Станции").click()
        await expect(owner.get_by_role("button", name="Создать станцию")).to_be_visible()
        await owner.screenshot(path=SH + "05_stations.png", full_page=True)
        await owner.get_by_role("button", name="Отчёты").click()
        await owner.get_by_role("button", name="Показать").click()
        await expect(owner.locator(".chip", has_text="Выручка")).to_be_visible()
        await owner.screenshot(path=SH + "06_reports.png", full_page=True)
        await owner.get_by_role("button", name="Сайт-меню").click()
        await owner.get_by_role("button", name="ky").click()
        await expect(owner.get_by_text("Палоо")).to_be_visible()
        await owner.screenshot(path=SH + "07_site.png", full_page=True)
        await owner.get_by_role("button", name="События").click()
        evs = await owner.locator(".ev").count()
        print("owner ws events", evs)
        await owner.get_by_role("button", name="Персонал").click()
        await expect(owner.get_by_role("heading", name="Роли и права")).to_be_visible()
        await owner.screenshot(path=SH + "08_staff.png", full_page=True)
        await br.close()
    print("ERRORS:", errors or "none")

asyncio.run(main())
