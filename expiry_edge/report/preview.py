import asyncio, sys
from playwright.async_api import async_playwright
body = open("expiry_edge.html").read()
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for theme in ["light","dark"]:
            html = f'<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8"></head><body>{body}</body></html>'
            open(f"preview_{theme}.html","w").write(html)
            pg = await b.new_page(viewport={"width":1280,"height":900})
            await pg.goto("file:///root/expiry_edge/report/preview_"+theme+".html")
            await pg.wait_for_timeout(1500)
            await pg.screenshot(path=f"shot_{theme}_full.png", full_page=True)
            h = await pg.evaluate("document.body.scrollHeight")
            print(theme, "height", h)
            await pg.close()
        pg = await b.new_page(viewport={"width":420,"height":900})
        await pg.goto("file:///root/expiry_edge/report/preview_light.html")
        await pg.wait_for_timeout(1000)
        w = await pg.evaluate("document.documentElement.scrollWidth")
        print("mobile scrollWidth", w)
        await pg.screenshot(path="shot_mobile.png", full_page=False)
        await b.close()
asyncio.run(main())
