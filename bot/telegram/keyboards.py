from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⚙️ Статус")],
            [KeyboardButton(text="🗂 Очередь"), KeyboardButton(text="⏸ Пауза / ▶️ Продолжить")]
        ],
        resize_keyboard=True,
        persistent=True
    )

def get_review_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"act:{item_id}:approve"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"act:{item_id}:reject")
            ]
        ]
    )
