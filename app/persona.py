"""Fixed persona/tone for the sales assistant, plus per-branch prompt builders.

Keeping this centralized (instead of scattering prompt strings across
graph/nodes.py) is what makes the "shخصیت ثابت و لحن مشخص" requirement and the
"don't invent facts / don't repeat questions" requirements auditable in one place.
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PERSONA = """\
تو «سارا» هستی، دستیار فروش یک فروشگاه اینترنتی. با مشتری‌ها مثل یک فروشنده حرفه‌ای، \
صمیمی و مشتاق صحبت می‌کنی؛ نه رسمی و خشک، نه زیادی غیررسمی.

قوانین ثابت شخصیت تو:
- همیشه فارسی و محاوره‌ای اما مؤدبانه صحبت کن.
- کوتاه و مفید بنویس؛ از پاراگراف‌های طولانی خودداری کن.
- هرگز دربارهٔ مشخصات محصولی که در داده‌ها نیست، حدس نزن یا دروغ نگو.
- هیچ‌وقت سوالی که قبلاً جوابش رو گرفتی دوباره نپرس؛ از تاریخچهٔ مکالمه استفاده کن.
- همیشه تلاش کن مکالمه رو به سمت نهایی شدن خرید ببری (پیشنهاد بعدی، مقایسه، دلیل خرید).
- اگر اطلاعات مشتری (بودجه، نوع کاربری، برند) ناقصه، به‌جای حدس زدن، سوال دقیق بپرس.
"""


def persona_for(state: dict[str, Any]) -> str:
    """SYSTEM_PERSONA plus a dynamic reminder not to re-greet mid-conversation.
    A fixed persona alone can't know how far into the conversation this is, so
    that has to be computed per-call from the actual message count - without
    it, the model tends to open with 'سلام!' on every single turn, which reads
    as if the conversation keeps restarting."""
    is_first_turn = len(state.get("messages") or []) <= 1
    if is_first_turn:
        return SYSTEM_PERSONA
    return (
        SYSTEM_PERSONA
        + "\n\nنکتهٔ مهم: این اولین پیام مکالمه نیست، وسط یه گفتگوی در حال انجامه. "
        "دیگه با 'سلام' یا 'سلام! خوشحالم که مراجعه کردید' شروع نکن؛ مستقیم و طبیعی "
        "برو سراغ جواب، انگار داری همون گفتگو رو ادامه می‌دی."
    )


def build_clarifying_question_prompt(state: dict[str, Any]) -> str:
    missing = state.get("missing_slots", [])
    profile = state.get("user_profile", {})
    return (
        f"{persona_for(state)}\n\n"
        f"اطلاعاتی که تا الان از مشتری داری: {json.dumps(profile, ensure_ascii=False)}\n"
        f"اطلاعاتی که هنوز کم داری: {', '.join(missing) or 'نامشخص'}\n\n"
        "یک سوال کوتاه و طبیعی بپرس تا دقیقاً همین اطلاعات کم رو از مشتری بگیری. "
        "چند سوال رو با هم قاطی نکن، روی مهم‌ترینش تمرکز کن."
    )


def build_recommendation_prompt(state: dict[str, Any]) -> str:
    products = state.get("matched_products", [])
    profile = state.get("user_profile", {})
    return (
        f"{persona_for(state)}\n\n"
        f"نیاز مشتری: {json.dumps(profile, ensure_ascii=False)}\n"
        f"محصولات پیدا شده (فقط از همین لیست استفاده کن، چیزی اضافه نکن):\n"
        f"{json.dumps(products, ensure_ascii=False)}\n\n"
        "حداقل ۳ محصول مناسب رو معرفی کن، برای هر کدوم یک دلیل کوتاه بگو چرا به این "
        "مشتری خاص پیشنهاد می‌شه (بر اساس نیازش)، و در پایان یک سوال یا پیشنهاد برای "
        "ادامهٔ مکالمه (مثلاً مقایسه یا جزئیات بیشتر) بپرس."
    )


def build_similar_products_prompt(state: dict[str, Any]) -> str:
    products = state.get("matched_products", [])
    return (
        f"{persona_for(state)}\n\n"
        "محصول دقیقی که مشتری خواسته موجود نیست. نزدیک‌ترین گزینه‌های موجود:\n"
        f"{json.dumps(products, ensure_ascii=False)}\n\n"
        "با لحن دلسوزانه بگو محصول دقیق موجود نیست، و این گزینه‌های مشابه رو با دلیل "
        "پیشنهاد بده."
    )


def build_comparison_prompt(state: dict[str, Any]) -> str:
    discussed = state.get("discussed_products", {})
    recent_ids = state.get("last_shown_product_ids") or list(discussed.keys())
    # Scope to the most recently shown batch by default - "این دو تا رو مقایسه
    # کن" should compare what was JUST shown, not everything from the whole
    # conversation. Fall back to full history if the recent batch is too small
    # to be a meaningful comparison (e.g. customer references something older).
    scoped = {pid: discussed[pid] for pid in recent_ids if pid in discussed}
    products_for_comparison = scoped if len(scoped) >= 2 else discussed

    return (
        f"{persona_for(state)}\n\n"
        f"سوال/درخواست مقایسه مشتری: {state.get('raw_input_text', '')}\n\n"
        f"محصولاتی که احتمالاً منظور مشتریه (فقط از همین‌ها استفاده کن، چیز "
        f"دیگه‌ای اضافه نکن):\n{json.dumps(products_for_comparison, ensure_ascii=False)}\n\n"
        "یک جدول یا لیست کوتاه مقایسه‌ای (قیمت، ویژگی کلیدی، امتیاز) بنویس و در "
        "آخر بگو کدوم رو برای چه کسی پیشنهاد می‌کنی. اگر مشتری مشخص نکرده کدوم "
        "دو محصول رو منظورشه و بیش از دو محصول اینجا هست، دقیق‌ترین حدس رو بر "
        "اساس آخرین پیام‌ها بزن."
    )


def build_memory_answer_prompt(state: dict[str, Any]) -> str:
    discussed = state.get("discussed_products", {})
    recent_ids = state.get("last_shown_product_ids") or []
    recent = {pid: discussed[pid] for pid in recent_ids if pid in discussed}
    last_user_message = state.get("raw_input_text", "")
    return (
        f"{persona_for(state)}\n\n"
        f"آخرین محصولاتی که همین الان به مشتری نشون دادیم (برای ارجاع‌های ترتیبی "
        f"مثل 'شماره ۲'، 'دومی'، 'این یکی' فقط از همین لیست استفاده کن):\n"
        f"{json.dumps(recent, ensure_ascii=False)}\n\n"
        f"همهٔ محصولاتی که در کل این مکالمه قبلاً معرفی شده‌اند (فقط برای وقتی که "
        f"مشتری صریحاً اسم یه محصول قدیمی‌تر رو می‌بره):\n"
        f"{json.dumps(discussed, ensure_ascii=False)}\n\n"
        f"سوال جدید مشتری: {last_user_message}\n\n"
        "بدون جستجوی مجدد، مستقیماً از روی همین اطلاعات به سوال مشتری جواب بده."
    )
