"""우리집 건강달력 — Streamlit / GitHub 배포용

실행: pip install -r requirements.txt && streamlit run app.py
배포: GitHub에 두 파일 업로드 → Streamlit Community Cloud → app.py 선택.

권장: Cloud의 Settings → Secrets에 아래 값을 설정합니다(저장소에 올리지 마세요).
APP_URL = "https://your-app.streamlit.app"
DATABASE_URL = "postgresql://user:password@host:5432/dbname?sslmode=require"
PostgreSQL DB는 미리 발급받아야 하며, 테이블은 앱이 자동 생성합니다.
DATABASE_URL이 없으면 로컬 SQLite를 사용합니다. Cloud의 로컬 파일은 재시작·
재배포 시 사라질 수 있으므로 실제 가족 일정에는 외부 PostgreSQL을 연결하세요.
QR은 앱을 설치하는 것이 아니라 배포된 앱을 브라우저에서 엽니다.
진료기록·주민번호 등 민감정보는 입력하지 마세요. 의료서비스가 아닌 일정 메모 앱입니다.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import io
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time
from urllib.parse import urlsplit, urlunsplit, urlencode

import qrcode
import streamlit as st

st.set_page_config(page_title="우리집 건강달력", page_icon="🌷", layout="centered", initial_sidebar_state="collapsed")


def setting(name, default=""):
    try:
        return str(st.secrets.get(name, os.environ.get(name, default)))
    except Exception:
        return os.environ.get(name, default)


DB_URL = setting("DATABASE_URL")


@contextmanager
def database():
    if DB_URL:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(DB_URL, row_factory=dict_row, connect_timeout=10)
    else:
        conn = sqlite3.connect("family_calendar.db", timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(conn, sql, params=()):
    return conn.execute(sql.replace("?", "%s") if DB_URL else sql, params)


@st.cache_resource
def initialize_db():
    with database() as conn:
        execute(conn, "CREATE TABLE IF NOT EXISTS families (id TEXT PRIMARY KEY, name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL)")
        execute(conn, "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, family_id TEXT NOT NULL, day TEXT NOT NULL, clock TEXT NOT NULL, title TEXT NOT NULL, member TEXT NOT NULL, category TEXT NOT NULL, memo TEXT NOT NULL)")
        execute(conn, "CREATE INDEX IF NOT EXISTS events_family_day ON events(family_id, day)")
    return True


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()


def fetch_events(start, end):
    with database() as conn:
        return [dict(r) for r in execute(conn, "SELECT * FROM events WHERE family_id=? AND day>=? AND day<=? ORDER BY day, clock, title", (st.session_state.family_id, start, end)).fetchall()]


def shift_month(delta):
    d = st.session_state.month
    index = d.year * 12 + d.month - 1 + delta
    year, month = divmod(index, 12)
    if 1900 <= year <= 2100:
        st.session_state.month = date(year, month + 1, 1)


CSS = """
<style>
:root { color-scheme: light; }
.stApp { background: #fff9f4; color:#3c4540; }
[data-testid="stHeader"] { background:transparent; height:0; }
[data-testid="stToolbar"] { display:none; }
.block-container { max-width:510px; padding: .55rem .6rem .5rem !important; }
[data-testid="stVerticalBlock"] { gap:.34rem; }
[data-testid="stHorizontalBlock"] { gap:.22rem !important; flex-wrap:nowrap !important; }
[data-testid="stColumn"] { min-width:0 !important; }
[data-testid="stButton"] button { border:1px solid #eadfd5; border-radius:12px; background:#fffefa; color:#38463e; min-height:36px; padding:2px 3px; width:100%; box-shadow:none; }
[data-testid="stButton"] button p { font-size:12px; white-space:pre-line; line-height:1.12; }
[data-testid="stButton"] button[kind="primary"] { background:#d8eddf; color:#254a36; border-color:#a9cdb5; }
[data-testid="stPopover"] button { min-height:34px; border-radius:12px; padding:2px 8px; }
.title { font-size:21px; font-weight:800; margin:0; letter-spacing:-.6px; }
.sub { font-size:11px; color:#65736a; margin:0 0 3px; }
.month { text-align:center; font-size:17px; font-weight:800; padding:3px 0; }
.weekday { text-align:center; font-size:11px; color:#68766e; }
.legend { font-size:10px; text-align:center; color:#617267; padding:3px 0; }
.note { padding:7px 10px; border-radius:12px; background:#f0eee3; font-size:11px; color:#53634f; }
[data-testid="stDialog"] [role="dialog"] { max-height:88dvh; overflow-y:auto; background:#fffaf6; }
@media(max-width:480px) {
 .block-container { padding-top:.35rem !important; }
 [data-testid="stButton"] button { min-height:34px; border-radius:9px; }
 .title {font-size:19px;}
}
@media(max-height:680px) {
 [data-testid="stVerticalBlock"] {gap:.2rem;}
 [data-testid="stButton"] button {min-height:30px;}
 .title {font-size:18px;}
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
try:
    initialize_db()
except Exception:
    st.error("저장소에 연결할 수 없어요. DATABASE_URL 설정 또는 데이터베이스 상태를 확인해주세요.")
    st.stop()

if "month" not in st.session_state:
    st.session_state.month = date.today().replace(day=1)

ICONS = {"병원 예약": "🏥", "복약·접종": "💊", "중요 일정": "⭐", "자유 기록": "📝"}


@st.dialog("🌷 하루 기록", width="small")
def day_dialog(chosen):
    st.caption(f"{chosen:%Y년 %m월 %d일} · 가족 모두에게 공유됩니다")
    rows = fetch_events(chosen.isoformat(), chosen.isoformat())
    options = ["새 기록"] + [r["id"] for r in rows]
    labels = {r["id"]: f'{ICONS.get(r["category"], "📝")} {r["clock"]} {r["title"]}' for r in rows}
    selected = st.selectbox("기록 선택", options, format_func=lambda v: labels.get(v, "＋ 새 기록 작성"))
    current = next((r for r in rows if r["id"] == selected), None)
    key = selected + chosen.isoformat()
    with st.form("event_" + key):
        title = st.text_input("제목 *", value=current["title"] if current else "", max_chars=60, placeholder="예: 소아과 정기검진")
        a, b = st.columns(2)
        with a:
            member = st.text_input("가족 이름", value=current["member"] if current else "", max_chars=20, placeholder="예: 하윤")
            category = st.selectbox("종류", list(ICONS), index=list(ICONS).index(current["category"]) if current and current["category"] in ICONS else 0)
        with b:
            event_date = st.date_input("날짜", value=date.fromisoformat(current["day"]) if current else chosen, min_value=date(1900, 1, 1), max_value=date(2100, 12, 31))
            event_time = st.time_input("시간", value=time.fromisoformat(current["clock"]) if current and current["clock"] else time(9, 0), step=300)
        memo = st.text_area("자유 메모", value=current["memo"] if current else "", max_chars=1500, height=85, placeholder="준비물, 병원 위치, 가족에게 전할 말…")
        save = st.form_submit_button("💚 저장", use_container_width=True, type="primary")
    if save:
        if not title.strip():
            st.warning("제목을 적어주세요.")
        else:
            try:
                with database() as conn:
                    values = (event_date.isoformat(), event_time.strftime("%H:%M"), title.strip(), member.strip(), category, memo.strip())
                    if current:
                        execute(conn, "UPDATE events SET day=?, clock=?, title=?, member=?, category=?, memo=? WHERE id=? AND family_id=?", values + (current["id"], st.session_state.family_id))
                    else:
                        execute(conn, "INSERT INTO events (day,clock,title,member,category,memo,id,family_id) VALUES (?,?,?,?,?,?,?,?)", values + (uuid.uuid4().hex, st.session_state.family_id))
                st.rerun()
            except Exception:
                st.error("저장하지 못했어요. 연결 상태를 확인하고 다시 시도해주세요.")
    if current:
        confirm = st.checkbox("이 기록을 삭제할게요", key="delete_" + key)
        if st.button("🗑 삭제", disabled=not confirm, use_container_width=True):
            try:
                with database() as conn:
                    execute(conn, "DELETE FROM events WHERE id=? AND family_id=?", (current["id"], st.session_state.family_id))
                st.rerun()
            except Exception:
                st.error("삭제하지 못했어요. 잠시 후 다시 시도해주세요.")


st.markdown('<div class="title">🌷 우리집 건강달력</div><div class="sub">작은 기록으로, 우리 가족을 더 다정하게</div>', unsafe_allow_html=True)

if "family_id" not in st.session_state:
    st.caption("가족끼리 같은 코드와 비밀번호로 접속하세요.")
    join, create = st.tabs(["🏡 가족 참여", "🌱 새 가족"])
    with join:
        with st.form("join"):
            code = st.text_input("가족 코드", value=str(st.query_params.get("family", ""))[:32], max_chars=32).strip().upper()
            password = st.text_input("가족 비밀번호", type="password", max_chars=128)
            submitted = st.form_submit_button("달력 열기", use_container_width=True, type="primary")
        if submitted:
            now = datetime.now().timestamp()
            if now < st.session_state.get("blocked_until", 0):
                st.warning("시도가 많아요. 30초 뒤 다시 시도해주세요.")
            else:
                with database() as conn:
                    family = execute(conn, "SELECT * FROM families WHERE id=?", (code,)).fetchone()
                if family and hmac.compare_digest(family["password_hash"], password_hash(password, family["salt"])):
                    st.session_state.family_id = code
                    st.session_state.family_name = family["name"]
                    st.session_state.attempts = 0
                    st.rerun()
                else:
                    st.session_state.attempts = st.session_state.get("attempts", 0) + 1
                    if st.session_state.attempts % 5 == 0:
                        st.session_state.blocked_until = now + 30
                    st.error("가족 코드 또는 비밀번호를 확인해주세요.")
    with create:
        with st.form("create"):
            name = st.text_input("가족 이름", placeholder="예: 토끼네 가족", max_chars=24)
            new_password = st.text_input("새 비밀번호 (8자 이상)", type="password", max_chars=128)
            repeat = st.text_input("비밀번호 확인", type="password", max_chars=128)
            made = st.form_submit_button("우리 가족 달력 만들기", use_container_width=True, type="primary")
        if made:
            if not name.strip() or len(new_password) < 8 or new_password != repeat:
                st.warning("가족 이름과 8자 이상 비밀번호를 입력하고, 비밀번호 확인을 맞춰주세요.")
            else:
                family_id = secrets.token_hex(5).upper()
                salt = secrets.token_hex(16)
                with database() as conn:
                    execute(conn, "INSERT INTO families (id,name,salt,password_hash) VALUES (?,?,?,?)", (family_id, name.strip(), salt, password_hash(new_password, salt)))
                st.session_state.family_id = family_id
                st.session_state.family_name = name.strip()
                st.rerun()
    st.caption("🔒 비밀번호는 암호화 해시로 저장됩니다. 비밀번호 복구 기능은 없으니 안전하게 보관하세요.")
    if not DB_URL:
        st.caption("⚠️ 현재 임시·로컬 저장 모드입니다. 실제 배포에는 외부 DB를 연결하세요.")
    st.stop()

left, middle, right = st.columns([1, 1, 1])
with left:
    if st.button("＋ 오늘 기록", use_container_width=True):
        day_dialog(date.today())
with middle:
    with st.popover("📱 QR 공유", use_container_width=True):
        st.write(st.session_state.family_name)
        st.code(st.session_state.family_id, language=None)
        st.caption("가족 코드는 QR에 포함되지만 비밀번호는 포함되지 않습니다. 비밀번호는 가족에게 별도로 알려주세요.")
        base = st.text_input("배포한 앱 주소", value=setting("APP_URL"), placeholder="https://your-app.streamlit.app")
        parsed = urlsplit(base.strip())
        if parsed.scheme in ("https", "http") and parsed.netloc and not parsed.username and not parsed.password:
            link = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode({"family": st.session_state.family_id}), ""))
            buffer = io.BytesIO()
            qrcode.make(link).save(buffer, format="PNG")
            st.image(buffer.getvalue(), width=190)
            st.text_input("가족 초대 링크", value=link, disabled=True)
            st.download_button("QR 이미지 받기", buffer.getvalue(), file_name="family_qr.png", mime="image/png", use_container_width=True)
        else:
            st.caption("공개 배포 주소를 넣으면 QR이 만들어집니다. localhost 주소는 다른 스마트폰에서 열리지 않아요.")
with right:
    with st.popover("⚙️ 관리", use_container_width=True):
        st.write(st.session_state.family_name)
        st.caption("가족 코드")
        st.code(st.session_state.family_id, language=None)
        st.caption("수정 사항은 다른 기기에서 새로고침하면 반영됩니다. 이 앱은 알림·진단 기능을 제공하지 않습니다.")
        if not DB_URL:
            st.warning("로컬 DB 모드: Cloud 재시작 시 데이터가 사라질 수 있어요.")
        if st.button("🔄 일정 새로고침", use_container_width=True):
            st.rerun()
        if st.button("로그아웃", use_container_width=True):
            for key in ("family_id", "family_name"):
                st.session_state.pop(key, None)
            st.rerun()

nav1, nav2, nav3 = st.columns([1, 5, 1])
with nav1:
    if st.button("‹", key="prev", help="이전 달"):
        shift_month(-1)
        st.rerun()
with nav2:
    st.markdown(f'<div class="month">{st.session_state.month:%Y년 %m월}</div>', unsafe_allow_html=True)
with nav3:
    if st.button("›", key="next", help="다음 달"):
        shift_month(1)
        st.rerun()

month = st.session_state.month
last = calendar.monthrange(month.year, month.month)[1]
try:
    rows = fetch_events(month.isoformat(), month.replace(day=last).isoformat())
except Exception:
    st.error("일정을 불러오지 못했어요. 새로고침해주세요.")
    st.stop()
by_day = {}
for event in rows:
    by_day.setdefault(event["day"], []).append(event)
for col, label in zip(st.columns(7), ["월", "화", "수", "목", "금", "토", "일"]):
    with col:
        st.markdown(f'<div class="weekday">{label}</div>', unsafe_allow_html=True)
weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(month.year, month.month)
for week_index, week in enumerate(weeks):
    for col, number in zip(st.columns(7), week):
        with col:
            if not number:
                st.markdown('<div style="height:34px"></div>', unsafe_allow_html=True)
                continue
            chosen = month.replace(day=number)
            items = by_day.get(chosen.isoformat(), [])
            suffix = "" if not items else "\n" + "".join(dict.fromkeys(ICONS.get(item["category"], "📝") for item in items))[:2]
            label = str(number) + suffix
            hint = "\n".join(f'{r["clock"]} {r["member"]} {r["title"]}' for r in items) or "기록 남기기"
            if st.button(label, key=f"day_{chosen}", help=hint, type="primary" if chosen == date.today() else "secondary", use_container_width=True):
                day_dialog(chosen)
st.markdown('<div class="legend">🏥 병원　💊 복약·접종　⭐ 중요　📝 메모 · 날짜를 눌러 기록</div>', unsafe_allow_html=True)
footer1, footer2 = st.columns([4, 1])
with footer1:
    today_items = by_day.get(date.today().isoformat(), [])
    text = f"이번 달 {len(rows)}개 기록 · 오늘 {len(today_items)}개" if month == date.today().replace(day=1) else f"이번 달 {len(rows)}개 기록"
    st.markdown(f'<div class="note">💚 {text}</div>', unsafe_allow_html=True)
with footer2:
    if st.button("오늘", help="이번 달로 이동"):
        st.session_state.month = date.today().replace(day=1)
        st.rerun()
