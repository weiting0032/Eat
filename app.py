import html
import logging
import os
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ai_service import analyze_food_image
from config import settings, validate_settings
from db import (
    get_today_meals,
    get_today_summary,
    get_user,
    init_db,
    save_meal,
    set_goal,
    upsert_user,
)

from config import settings

print("AI_PROVIDER:", settings.ai_provider)
if settings.gemini_api_key:
    print("GEMINI_API_KEY suffix:", settings.gemini_api_key[-6:])
print("GEMINI_MODEL:", settings.gemini_model)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def fmt_num(value):
    try:
        value = float(value)
    except Exception:
        return "0"

    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def calc_macro_percentages(carbs_g: float, protein_g: float, fat_g: float):
    carbs_kcal = carbs_g * 4
    protein_kcal = protein_g * 4
    fat_kcal = fat_g * 9
    total = carbs_kcal + protein_kcal + fat_kcal

    if total <= 0:
        return 0, 0, 0

    carb_pct = round((carbs_kcal / total) * 100)
    protein_pct = round((protein_kcal / total) * 100)
    fat_pct = max(0, 100 - carb_pct - protein_pct)
    return carb_pct, protein_pct, fat_pct


def build_result_message(result: dict, today_summary: dict, target_kcal: int) -> str:
    items = result.get("items", [])
    total_kcal = float(result.get("total_calories_kcal", 0))
    carbs_g = float(result.get("macros", {}).get("carbs_g", 0))
    protein_g = float(result.get("macros", {}).get("protein_g", 0))
    fat_g = float(result.get("macros", {}).get("fat_g", 0))
    confidence = float(result.get("confidence", 0))
    advice = result.get("advice", [])
    notes = result.get("notes", [])

    carb_pct, protein_pct, fat_pct = calc_macro_percentages(carbs_g, protein_g, fat_g)

    today_total = float(today_summary.get("total_calories_kcal", 0))
    remaining = target_kcal - today_total

    item_lines = []
    for item in items[:8]:
        name = html.escape(str(item.get("name", "未辨識食物")))
        weight = fmt_num(item.get("estimated_weight_g", 0))
        kcal = fmt_num(item.get("calories_kcal", 0))
        item_lines.append(f"• {name}：{weight}g，{kcal} kcal")

    if not item_lines:
        item_lines.append("• AI 無法拆解細項，以下為整體估算")

    advice_lines = []
    for idx, line in enumerate(advice[:3], start=1):
        advice_lines.append(f"{idx}. {html.escape(str(line))}")

    notes_lines = []
    for line in notes[:3]:
        notes_lines.append(f"• {html.escape(str(line))}")

    if remaining >= 0:
        remain_text = f"今日尚可攝取：約 <b>{fmt_num(remaining)}</b> kcal"
    else:
        remain_text = f"今日已超標：約 <b>{fmt_num(abs(remaining))}</b> kcal"

    text = f"""
<b>📷 本次餐點分析結果</b>

<b>餐點名稱</b>
{html.escape(str(result.get("meal_name", "本次餐點")))}

<b>食物內容</b>
{chr(10).join(item_lines)}

<b>總熱量</b>
🔥 <b>{fmt_num(total_kcal)} kcal</b>

<b>三大營養素</b>
碳水 {fmt_num(carbs_g)}g / 蛋白質 {fmt_num(protein_g)}g / 脂肪 {fmt_num(fat_g)}g

<b>營養比例</b>
碳水 {carb_pct}% / 蛋白質 {protein_pct}% / 脂肪 {fat_pct}%

<b>今日累計</b>
已記錄 <b>{today_summary.get("meal_count", 0)}</b> 餐
今日總熱量：約 <b>{fmt_num(today_total)}</b> kcal
目標熱量：<b>{fmt_num(target_kcal)}</b> kcal
{remain_text}

<b>建議</b>
{chr(10).join(advice_lines) if advice_lines else "1. 建議補充份量說明以提高準確度"}

<b>備註</b>
{chr(10).join(notes_lines) if notes_lines else "• 本結果為 AI 依照片估算，實際數值可能因烹調方式與份量而有差異"}
    
<b>辨識信心</b>
{fmt_num(confidence * 100)}%
""".strip()

    return text


def build_today_summary_message(chat_id: int) -> str:
    user = get_user(chat_id)
    target_kcal = user["daily_calorie_target"] if user else settings.default_daily_calorie_target
    summary = get_today_summary(chat_id)
    meals = get_today_meals(chat_id, limit=10)

    total_kcal = float(summary.get("total_calories_kcal", 0))
    carbs_g = float(summary.get("carbs_g", 0))
    protein_g = float(summary.get("protein_g", 0))
    fat_g = float(summary.get("fat_g", 0))
    meal_count = int(summary.get("meal_count", 0))

    carb_pct, protein_pct, fat_pct = calc_macro_percentages(carbs_g, protein_g, fat_g)

    remaining = target_kcal - total_kcal
    if remaining >= 0:
        remain_text = f"今日尚可攝取：約 <b>{fmt_num(remaining)}</b> kcal"
    else:
        remain_text = f"今日已超標：約 <b>{fmt_num(abs(remaining))}</b> kcal"

    meal_lines = []
    for idx, meal in enumerate(meals, start=1):
        name = html.escape(str(meal.get("meal_name", "本次餐點")))
        kcal = fmt_num(meal.get("total_calories_kcal", 0))
        created_at = str(meal.get("created_at", ""))
        meal_time = created_at[11:16] if len(created_at) >= 16 else "--:--"
        meal_lines.append(f"{idx}. {meal_time}｜{name}｜{kcal} kcal")

    if not meal_lines:
        meal_lines.append("今天尚未有任何餐點紀錄。")

    text = f"""
<b>📊 今日統計</b>

已記錄餐數：<b>{meal_count}</b>
今日總熱量：<b>{fmt_num(total_kcal)}</b> kcal
目標熱量：<b>{fmt_num(target_kcal)}</b> kcal
{remain_text}

<b>三大營養素累計</b>
碳水 {fmt_num(carbs_g)}g / 蛋白質 {fmt_num(protein_g)}g / 脂肪 {fmt_num(fat_g)}g

<b>營養比例</b>
碳水 {carb_pct}% / 蛋白質 {protein_pct}% / 脂肪 {fat_pct}%

<b>今日餐點明細</b>
{chr(10).join(meal_lines)}
""".strip()

    return text


def ensure_user(update: Update):
    user = update.effective_user
    chat = update.effective_chat
    upsert_user(
        chat_id=chat.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = """
👋 歡迎使用 AI 飲食紀錄 Bot

你可以直接：
1. 上傳餐點照片
2. 可在照片 caption 補充說明，例如：
   - 飯半碗
   - 無糖豆漿 300ml
   - 雞腿去皮

我會回傳：
• 食物內容
• 推估份量
• 總熱量
• 三大營養素
• 今日累計與建議

可用指令：
/today - 查看今日統計
/setgoal 1500 - 設定每日熱量目標
/help - 查看說明
""".strip()
    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def setgoal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    if not context.args:
        await update.message.reply_text("請輸入目標熱量，例如：/setgoal 1500")
        return

    try:
        goal = int(context.args[0])
        if goal <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("格式錯誤，請輸入正整數，例如：/setgoal 1500")
        return

    set_goal(update.effective_chat.id, goal)
    await update.message.reply_text(f"✅ 已更新每日熱量目標為 {goal} kcal")


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = build_today_summary_message(update.effective_chat.id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text and not update.message.text.startswith("/"):
        await update.message.reply_text(
            "請直接上傳餐點照片給我分析。\n"
            "你也可以在照片描述中補充：例如「飯半碗、無糖豆漿 300ml」。"
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    message = update.message
    if not message or not message.photo:
        return

    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    target_kcal = user["daily_calorie_target"] if user else settings.default_daily_calorie_target

    status_msg = await message.reply_text("🧠 已收到照片，AI 分析中，請稍候 5~15 秒...")

    try:
        photo = message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)

        now = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y%m%d_%H%M%S")
        filename = f"{chat_id}_{now}_{uuid4().hex[:8]}.jpg"
        image_path = os.path.join(settings.upload_dir, filename)

        await tg_file.download_to_drive(custom_path=image_path)

        user_note = message.caption or ""
        result = analyze_food_image(image_path=image_path, user_note=user_note)

        save_meal(
            chat_id=chat_id,
            image_path=image_path,
            user_note=user_note,
            result=result,
        )

        today_summary = get_today_summary(chat_id)

        response_text = build_result_message(
            result=result,
            today_summary=today_summary,
            target_kcal=target_kcal,
        )

        await status_msg.edit_text(
            response_text,
            parse_mode=ParseMode.HTML,
        )

    except Exception:
        logger.exception("photo_handler failed")
        await status_msg.edit_text(
            "❌ 分析失敗，請稍後再試。\n"
            "可能原因：AI API 金鑰未設定、圖片格式不支援、或模型暫時無回應。"
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while handling update", exc_info=context.error)


def main():
    validate_settings()
    os.makedirs(settings.upload_dir, exist_ok=True)
    init_db()

    application = Application.builder().token(settings.telegram_bot_token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("setgoal", setgoal_command))

    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler)
    )

    application.add_error_handler(error_handler)

    logger.info("Telegram bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
