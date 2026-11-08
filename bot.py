import os
import asyncio
import tempfile
from pathlib import Path
from functools import lru_cache
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from rembg import remove, new_session

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = BASE_DIR / "fonts" / "NotoSansEthiopic-Black.ttf"

TEXT = "ቅድስና ለእግዚአብሔር"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing")

if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is missing")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET environment variable is missing")

if not FONT_PATH.is_file():
    raise FileNotFoundError(f"Font not found: {FONT_PATH}")


@lru_cache(maxsize=1)
def get_session():
    return new_session("isnet-general-use")


def edit_photo(input_path, output_path):
    # Correct camera orientation.
    original = Image.open(input_path)
    original = ImageOps.exif_transpose(original).convert("RGB")

    # Reduce memory usage.
    original.thumbnail((1600, 1600), Image.Resampling.LANCZOS)

    # Separate the person from the background.
    cutout = remove(
        original,
        session=get_session()
    ).convert("RGBA")

    # Blur the original background.
    blurred = original.filter(
        ImageFilter.GaussianBlur(radius=18)
    ).convert("RGBA")

    # Combine the sharp person and blurred background.
    result = Image.alpha_composite(blurred, cutout)

    width, height = result.size

    # Efficient dark gradient at the bottom.
    gradient_height = int(height * 0.27)
    gradient = Image.new("RGBA", result.size, (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)

    start_y = height - gradient_height

    for y in range(gradient_height):
        alpha = int(220 * y / gradient_height)

        gradient_draw.line(
            (0, start_y + y, width, start_y + y),
            fill=(0, 0, 0, alpha)
        )

    result = Image.alpha_composite(result, gradient)

    # Prepare bold Amharic text.
    draw = ImageDraw.Draw(result)
    font_size = int(width * 0.085)

    font = ImageFont.truetype(str(FONT_PATH), font_size)

    while font_size > 15:
        bbox = draw.textbbox(
            (0, 0),
            TEXT,
            font=font,
            stroke_width=2
        )

        if bbox[2] - bbox[0] <= width * 0.92:
            break

        font_size -= 2
        font = ImageFont.truetype(str(FONT_PATH), font_size)

    bbox = draw.textbbox((0, 0), TEXT, font=font)

    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (width - text_width) // 2 - bbox[0]
    y = (
        height
        - int(gradient_height * 0.65)
        - text_height // 2
        - bbox[1]
    )

    # Text shadow.
    draw.text(
        (x + 3, y + 4),
        TEXT,
        font=font,
        fill=(0, 0, 0, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 255)
    )

    # White Amharic text.
    draw.text(
        (x, y),
        TEXT,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255)
    )

    result.convert("RGB").save(
        output_path,
        format="JPEG",
        quality=95
    )


# Telegram commands.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🙏 Welcome to Holiness Photo Bot!\n\n"
        "Send me your photo.\n"
        "I will blur the background and add:\n\n"
        "ቅድስና ለእግዚአብሔር\n\n"
        "✨ Your edited photo will be returned shortly."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a photo as an image to get your edited portrait."
    )


async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.message

    if not message or not message.photo:
        return

    await message.reply_text(
        "🙏 Your photo is being edited. Please wait..."
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = str(Path(temp_dir) / "original.jpg")
        output_path = str(Path(temp_dir) / "edited.jpg")

        try:
            photo = message.photo[-1]

            telegram_file = await context.bot.get_file(
                photo.file_id
            )

            await telegram_file.download_to_drive(
                custom_path=input_path
            )

            await asyncio.to_thread(
                edit_photo,
                input_path,
                output_path
            )

            with open(output_path, "rb") as image:
                await message.reply_photo(
                    photo=image,
                    caption="✨ ቅድስና ለእግዚአብሔር"
                )

        except Exception as error:
            print("Photo processing error:", error)

            await message.reply_text(
                "Sorry, I couldn't edit this photo. "
                "Please try another photo."
            )


# Build Telegram application.
telegram_app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .build()
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", help_command))

telegram_app.add_handler(
    MessageHandler(filters.PHOTO, handle_photo)
)


# FastAPI startup and shutdown.
@asynccontextmanager
async def lifespan(api: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()

    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/telegram",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES
    )

    print("Holiness Photo Bot webhook is running!")

    try:
        yield
    finally:
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


# Render health check.
@app.get("/health")
async def health():
    return {"status": "ok", "bot": "Holiness Photo Bot"}


# Telegram webhook endpoint.
@app.post("/telegram")
async def telegram_webhook(request: Request):

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if received_secret != WEBHOOK_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook secret"
        )

    try:
        data = await request.json()

        update = Update.de_json(
            data,
            telegram_app.bot
        )

        await telegram_app.process_update(update)

        return {"ok": True}

    except Exception as error:
        print("Webhook error:", error)

        raise HTTPException(
            status_code=500,
            detail="Could not process update"
        )
