import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://mini.flashin.store")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN or TELEGRAM_BOT_TOKEN is not configured")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def shop_keyboard(product_id: int | None = None):
    url = MINI_APP_URL if product_id is None else f"{MINI_APP_URL}?product={product_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Открыть магазин FLASHIN", web_app=WebAppInfo(url=url))]
    ])


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "FLASHIN Store: выбор и покупка одежды прямо внутри Telegram.",
        reply_markup=shop_keyboard(),
    )


@dp.message(Command("product"))
async def product(message: Message):
    parts = message.text.split()
    product_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    await message.answer("Открыть товар:", reply_markup=shop_keyboard(product_id))


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
