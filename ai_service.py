import base64
import json
import mimetypes
import re
from typing import Any, Dict, List

from PIL import Image
import google.generativeai as genai
from openai import OpenAI

from config import settings


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        value = str(value).replace(",", "").strip()
        return float(value)
    except Exception:
        return default


def _extract_json_text(text: str) -> str:
    if not text:
        raise ValueError("模型未回傳內容")

    text = text.strip()

    # 優先處理 ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    # 再抓第一個 JSON 物件
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()

    raise ValueError(f"無法從模型輸出中解析 JSON：{text[:300]}")


def _safe_json_loads(text: str) -> Dict[str, Any]:
    json_text = _extract_json_text(text)
    return json.loads(json_text)


def _normalize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []

    normalized_items: List[Dict[str, Any]] = []
    total_by_items = 0.0

    for item in items:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "未辨識食物")).strip() or "未辨識食物"
        weight = _to_float(item.get("estimated_weight_g"), 0)
        kcal = _to_float(item.get("calories_kcal"), 0)

        total_by_items += kcal
        normalized_items.append(
            {
                "name": name,
                "estimated_weight_g": weight,
                "calories_kcal": kcal,
            }
        )

    macros = data.get("macros", {})
    if not isinstance(macros, dict):
        macros = {}

    carbs_g = _to_float(macros.get("carbs_g"), 0)
    protein_g = _to_float(macros.get("protein_g"), 0)
    fat_g = _to_float(macros.get("fat_g"), 0)

    total_calories_kcal = _to_float(data.get("total_calories_kcal"), 0)
    if total_calories_kcal <= 0 and total_by_items > 0:
        total_calories_kcal = total_by_items

    meal_name = str(data.get("meal_name", "")).strip()
    if not meal_name:
        if normalized_items:
            meal_name = "、".join([x["name"] for x in normalized_items[:3]])
        else:
            meal_name = "本次餐點"

    advice = data.get("advice", [])
    if not isinstance(advice, list):
        advice = [str(advice)]

    notes = data.get("notes", [])
    if not isinstance(notes, list):
        notes = [str(notes)]

    confidence = _to_float(data.get("confidence"), 0.6)
    confidence = max(0.0, min(1.0, confidence))

    normalized = {
        "meal_name": meal_name,
        "items": normalized_items,
        "total_calories_kcal": round(total_calories_kcal, 1),
        "macros": {
            "carbs_g": round(carbs_g, 1),
            "protein_g": round(protein_g, 1),
            "fat_g": round(fat_g, 1),
        },
        "confidence": round(confidence, 2),
        "advice": [str(x).strip() for x in advice if str(x).strip()][:3],
        "notes": [str(x).strip() for x in notes if str(x).strip()][:3],
    }

    if not normalized["advice"]:
        normalized["advice"] = ["本次結果為 AI 依照片估算，建議可再補充份量資訊以提高準確度。"]

    return normalized


def _build_prompt(user_note: str = "") -> str:
    return f"""
你是一位專業營養分析助手，請根據使用者上傳的食物照片，估算本餐的食物內容、份量、熱量與三大營養素。
請務必使用繁體中文。

請遵守以下規則：
1. 只輸出單一 JSON 物件，不要輸出 markdown、不要輸出 ```json。
2. 若照片中有多種食物，請拆成多個 items。
3. 必須估算每個品項的 estimated_weight_g 與 calories_kcal。
4. 必須提供 total_calories_kcal。
5. 必須提供 macros，其中包含 carbs_g、protein_g、fat_g。
6. confidence 請填 0 到 1 之間的小數，表示估算信心。
7. advice 提供最多 3 點簡短建議。
8. notes 提供最多 3 點補充說明，包含可能誤差來源。
9. 若無法完全辨識，仍需提供最合理估算，不要留空。
10. 如果使用者有補充說明，請優先參考補充內容。

使用者補充說明：
{user_note if user_note else "無"}

請輸出以下 JSON 格式：
{{
  "meal_name": "餐點名稱",
  "items": [
    {{
      "name": "食物名稱",
      "estimated_weight_g": 150,
      "calories_kcal": 250
    }}
  ],
  "total_calories_kcal": 550,
  "macros": {{
    "carbs_g": 55,
    "protein_g": 30,
    "fat_g": 20
  }},
  "confidence": 0.75,
  "advice": [
    "建議 1",
    "建議 2"
  ],
  "notes": [
    "說明 1",
    "說明 2"
  ]
}}
""".strip()


def _mime_type_from_path(image_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(image_path)
    return mime_type or "image/jpeg"


def _analyze_with_gemini(image_path: str, user_note: str = "") -> Dict[str, Any]:
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    img = Image.open(image_path)
    prompt = _build_prompt(user_note)

    response = model.generate_content(
        [prompt, img],
        generation_config={
            "temperature": 0.2,
            "top_p": 0.8,
            "top_k": 32,
            "max_output_tokens": 2048,
        },
    )

    text = ""
    if hasattr(response, "text") and response.text:
        text = response.text
    else:
        # fallback
        candidates = getattr(response, "candidates", []) or []
        parts = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts.append(part_text)
        text = "\n".join(parts).strip()

    parsed = _safe_json_loads(text)
    result = _normalize_result(parsed)
    result["provider"] = "gemini"
    return result


def _analyze_with_openai(image_path: str, user_note: str = "") -> Dict[str, Any]:
    client = OpenAI(api_key=settings.openai_api_key)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = _mime_type_from_path(image_path)
    prompt = _build_prompt(user_note)

    response = client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.2,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
    )

    text = response.choices[0].message.content or ""
    parsed = _safe_json_loads(text)
    result = _normalize_result(parsed)
    result["provider"] = "openai"
    return result


def analyze_food_image(image_path: str, user_note: str = "") -> Dict[str, Any]:
    if settings.ai_provider == "gemini":
        return _analyze_with_gemini(image_path, user_note)

    if settings.ai_provider == "openai":
        return _analyze_with_openai(image_path, user_note)

    raise ValueError(f"不支援的 AI_PROVIDER: {settings.ai_provider}")
