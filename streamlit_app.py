import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import settings
from db import get_conn, init_db

st.set_page_config(
    page_title="WonderFood 後台",
    page_icon="🥗",
    layout="wide",
)

init_db()


def check_password():
    admin_password = os.getenv("STREAMLIT_ADMIN_PASSWORD", "").strip()
    if not admin_password:
        return True

    def password_entered():
        if st.session_state.get("password", "") == admin_password:
            st.session_state["password_correct"] = True
            st.session_state.pop("password", None)
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.title("🔐 WonderFood 後台登入")
    st.text_input("請輸入後台密碼", type="password", on_change=password_entered, key="password")

    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("密碼錯誤")

    return False


if not check_password():
    st.stop()


def now_local():
    return datetime.now(ZoneInfo(settings.timezone))


def fetch_one(query, params=()):
    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else {}


def fetch_all_df(query, params=()):
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return pd.DataFrame([dict(r) for r in rows])


def build_where_clause(chat_id=None, start_date=None, end_date=None):
    where = ["1=1"]
    params = []

    if chat_id is not None:
        where.append("chat_id = ?")
        params.append(int(chat_id))

    if start_date:
        where.append("created_date >= ?")
        params.append(str(start_date))

    if end_date:
        where.append("created_date <= ?")
        params.append(str(end_date))

    return " AND ".join(where), params


@st.cache_data(ttl=60)
def load_users():
    return fetch_all_df(
        """
        SELECT chat_id, username, first_name, daily_calorie_target, created_at
        FROM users
        ORDER BY chat_id DESC
        """
    )


@st.cache_data(ttl=60)
def load_filtered_metrics(chat_id=None, start_date=None, end_date=None):
    where_sql, params = build_where_clause(chat_id, start_date, end_date)

    meal_metrics = fetch_one(
        f"""
        SELECT
            COUNT(*) AS total_meals,
            COUNT(DISTINCT chat_id) AS active_users,
            COALESCE(SUM(total_calories_kcal), 0) AS total_calories_kcal,
            COALESCE(SUM(carbs_g), 0) AS carbs_g,
            COALESCE(SUM(protein_g), 0) AS protein_g,
            COALESCE(SUM(fat_g), 0) AS fat_g
        FROM meal_records
        WHERE {where_sql}
        """,
        tuple(params),
    )

    all_users = fetch_one("SELECT COUNT(*) AS cnt FROM users")
    meal_metrics["registered_users"] = all_users.get("cnt", 0)
    return meal_metrics


@st.cache_data(ttl=60)
def load_daily_stats(chat_id=None, start_date=None, end_date=None):
    where_sql, params = build_where_clause(chat_id, start_date, end_date)

    df = fetch_all_df(
        f"""
        SELECT
            created_date,
            COUNT(*) AS meal_count,
            COALESCE(SUM(total_calories_kcal), 0) AS total_calories_kcal
        FROM meal_records
        WHERE {where_sql}
        GROUP BY created_date
        ORDER BY created_date
        """,
        tuple(params),
    )

    if not df.empty:
        df["created_date"] = pd.to_datetime(df["created_date"])
    return df


@st.cache_data(ttl=60)
def load_meals(chat_id=None, start_date=None, end_date=None, limit=300):
    where_sql, params = build_where_clause(chat_id, start_date, end_date)
    params.append(limit)

    return fetch_all_df(
        f"""
        SELECT
            id,
            chat_id,
            meal_name,
            image_path,
            user_note,
            total_calories_kcal,
            carbs_g,
            protein_g,
            fat_g,
            raw_ai_json,
            created_at,
            created_date
        FROM meal_records
        WHERE {where_sql}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(params),
    )


def format_kcal(v):
    try:
        v = float(v)
        if abs(v - round(v)) < 0.05:
            return str(int(round(v)))
        return f"{v:.1f}"
    except Exception:
        return "0"


def build_user_options(users_df: pd.DataFrame):
    options = {"全部使用者": None}
    if users_df.empty:
        return options

    for _, row in users_df.iterrows():
        chat_id = int(row["chat_id"])
        name = row.get("first_name") or row.get("username") or "未命名"
        label = f"{chat_id}｜{name}"
        options[label] = chat_id
    return options


st.title("🥗 WonderFood 管理後台")
st.caption("可查看使用者、餐點紀錄、熱量統計與 AI 分析結果")

with st.sidebar:
    st.header("查詢條件")

    if st.button("🔄 重新整理資料"):
        st.cache_data.clear()
        st.rerun()

    users_df = load_users()
    user_options = build_user_options(users_df)
    selected_user_label = st.selectbox("選擇使用者", list(user_options.keys()))
    selected_chat_id = user_options[selected_user_label]

    today = now_local().date()
    default_start = today - timedelta(days=6)

    start_date = st.date_input("開始日期", value=default_start)
    end_date = st.date_input("結束日期", value=today)

    limit = st.slider("最多顯示筆數", min_value=20, max_value=500, value=200, step=20)

    st.markdown("---")
    st.write("**系統資訊**")
    st.write(f"AI Provider：`{settings.ai_provider}`")
    st.write(f"DB：`{settings.db_path}`")
    st.write(f"Uploads：`{settings.upload_dir}`")
    st.write(f"Timezone：`{settings.timezone}`")

metrics = load_filtered_metrics(selected_chat_id, start_date, end_date)
daily_df = load_daily_stats(selected_chat_id, start_date, end_date)
meals_df = load_meals(selected_chat_id, start_date, end_date, limit=limit)

col1, col2, col3, col4 = st.columns(4)
col1.metric("註冊使用者數", int(metrics.get("registered_users", 0)))
col2.metric("期間活躍使用者", int(metrics.get("active_users", 0)))
col3.metric("餐點紀錄數", int(metrics.get("total_meals", 0)))
col4.metric("期間總熱量", f"{format_kcal(metrics.get('total_calories_kcal', 0))} kcal")

col5, col6, col7 = st.columns(3)
col5.metric("碳水總量", f"{format_kcal(metrics.get('carbs_g', 0))} g")
col6.metric("蛋白質總量", f"{format_kcal(metrics.get('protein_g', 0))} g")
col7.metric("脂肪總量", f"{format_kcal(metrics.get('fat_g', 0))} g")

st.markdown("---")

left, right = st.columns([1.3, 1])

with left:
    st.subheader("📈 每日熱量趨勢")
    if daily_df.empty:
        st.info("目前沒有符合條件的資料。")
    else:
        chart_df = daily_df.set_index("created_date")[["total_calories_kcal"]]
        st.line_chart(chart_df, height=300)

        st.subheader("📅 每日統計表")
        daily_show = daily_df.copy()
        daily_show["created_date"] = daily_show["created_date"].dt.strftime("%Y-%m-%d")
        daily_show.columns = ["日期", "餐數", "總熱量(kcal)"]
        st.dataframe(daily_show, use_container_width=True, hide_index=True)

with right:
    st.subheader("🥦 三大營養素總覽")
    macro_df = pd.DataFrame(
        {
            "營養素": ["碳水", "蛋白質", "脂肪"],
            "克數": [
                float(metrics.get("carbs_g", 0)),
                float(metrics.get("protein_g", 0)),
                float(metrics.get("fat_g", 0)),
            ],
        }
    ).set_index("營養素")
    st.bar_chart(macro_df, height=300)

st.markdown("---")
st.subheader("🧾 餐點紀錄清單")

if meals_df.empty:
    st.warning("目前沒有符合條件的餐點紀錄。")
else:
    show_df = meals_df.copy()
    show_df = show_df[
        [
            "id",
            "chat_id",
            "meal_name",
            "total_calories_kcal",
            "carbs_g",
            "protein_g",
            "fat_g",
            "created_at",
        ]
    ]
    show_df.columns = [
        "ID",
        "Chat ID",
        "餐點名稱",
        "熱量(kcal)",
        "碳水(g)",
        "蛋白質(g)",
        "脂肪(g)",
        "建立時間",
    ]
    st.dataframe(show_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("🔍 單筆紀錄查看")

    meal_map = {}
    options = []
    for _, row in meals_df.iterrows():
        label = f"#{row['id']}｜{row['created_at'][:16]}｜{row['meal_name']}"
        options.append(label)
        meal_map[label] = row.to_dict()

    selected_meal_label = st.selectbox("選擇一筆紀錄", options)
    selected_meal = meal_map[selected_meal_label]

    detail_col1, detail_col2 = st.columns([1, 1])

    with detail_col1:
        st.write(f"**紀錄 ID：** {selected_meal['id']}")
        st.write(f"**Chat ID：** {selected_meal['chat_id']}")
        st.write(f"**餐點名稱：** {selected_meal['meal_name']}")
        st.write(f"**建立時間：** {selected_meal['created_at']}")
        st.write(f"**熱量：** {format_kcal(selected_meal['total_calories_kcal'])} kcal")
        st.write(f"**碳水：** {format_kcal(selected_meal['carbs_g'])} g")
        st.write(f"**蛋白質：** {format_kcal(selected_meal['protein_g'])} g")
        st.write(f"**脂肪：** {format_kcal(selected_meal['fat_g'])} g")

        user_note = selected_meal.get("user_note") or ""
        if user_note.strip():
            st.write("**使用者補充說明：**")
            st.info(user_note)

        image_path = selected_meal.get("image_path") or ""
        if image_path:
            st.write(f"**圖片路徑：** `{image_path}`")

    with detail_col2:
        image_path = selected_meal.get("image_path") or ""
        if image_path and os.path.exists(image_path):
            st.image(image_path, caption="原始上傳圖片", use_container_width=True)
        else:
            st.warning("找不到圖片檔案，可能已不存在或部署環境未保留檔案。")

    st.markdown("---")
    st.subheader("🤖 AI 分析結果")

    raw_ai_json = selected_meal.get("raw_ai_json") or ""
    parsed = None

    if raw_ai_json.strip():
        try:
            parsed = json.loads(raw_ai_json)
        except Exception:
            st.error("raw_ai_json 不是合法 JSON")
            st.code(raw_ai_json, language="json")

    if isinstance(parsed, dict):
        st.json(parsed)

        items = parsed.get("items", [])
        if isinstance(items, list) and items:
            items_df = pd.DataFrame(items)
            if not items_df.empty:
                rename_map = {
                    "name": "食物名稱",
                    "estimated_weight_g": "估計重量(g)",
                    "calories_kcal": "熱量(kcal)",
                }
                items_df = items_df.rename(columns=rename_map)
                st.subheader("🍱 食物拆解")
                st.dataframe(items_df, use_container_width=True, hide_index=True)

        advice = parsed.get("advice", [])
        if isinstance(advice, list) and advice:
            st.subheader("💡 建議")
            for i, a in enumerate(advice, start=1):
                st.write(f"{i}. {a}")

        notes = parsed.get("notes", [])
        if isinstance(notes, list) and notes:
            st.subheader("📝 備註")
            for n in notes:
                st.write(f"- {n}")

st.markdown("---")
st.subheader("👤 使用者清單")

if users_df.empty:
    st.info("目前沒有使用者資料。")
else:
    user_show = users_df.copy()
    user_show.columns = ["Chat ID", "Username", "First Name", "每日熱量目標", "建立時間"]
    st.dataframe(user_show, use_container_width=True, hide_index=True)
