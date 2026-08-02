"""Validação executável da base Playwright sem acessar serviços externos."""

import asyncio

from app.collection import BrowserSession


async def main() -> None:
    async with BrowserSession() as session:
        page = await session.new_page()
        await page.set_content("<title>playwright-ok</title><h1>coleta</h1>")
        assert await page.title() == "playwright-ok"
        assert await page.locator("h1").text_content() == "coleta"


if __name__ == "__main__":
    asyncio.run(main())
