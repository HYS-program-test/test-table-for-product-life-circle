import streamlit as st
import pandas as pd
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime

st.set_page_config(page_title="商品生命週期儀錶板", page_icon="📊", layout="wide")

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTDEPT_SHEET_ID = "1hEt4uxBABBicxIMJuR57lMiigQYF02CQHZfB-Nc6vjo"  # Total Certificate Management

# ─────────────────────────────────────────────
# 資料載入
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_productdept_rows():
    """從 ProductDept 這份 Google Sheets 即時讀取到期清單，不做任何去重
    （同一個型號如果有多筆證書紀錄，全部都保留顯示）。
    如果還沒設定 Google 服務帳號，就退回讀本地備用的 JSON，不會讓頁面直接壞掉。"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        sa_info = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
        ws = sh.get_worksheet(0)
        values = ws.get_all_values()

        rows = []
        # 實際欄位（Total Certificate Management，商品驗證登錄部分）：
        # A=實驗室、B=類別、C=室外機型號、D=測試搭配(室內機)、E=(其他欄位，暫不使用)、
        # F=證書編號、G=有效期限
        # 畫面上的「Table_1」是 Google Sheets 表格功能的名稱標籤，不是資料列，
        # 所以第1列就是欄位標題，資料從第2列開始。
        # 用 padding 而不是「欄位數不足就整列跳過」，避免 Google Sheets API 回傳的
        # 某一列剛好比較短（尾端空白儲存格被省略）時，整筆資料被誤刪掉。
        for row in values[1:]:
            row = list(row) + [""] * (7 - len(row)) if len(row) < 7 else row
            model = row[2].strip()
            if not model:
                continue
            category, cert_no, expire_str = row[1].strip(), row[5].strip(), row[6].strip()
            if not expire_str:
                continue
            try:
                expire_date = datetime.strptime(expire_str, "%Y/%m/%d").date()
            except ValueError:
                try:
                    expire_date = datetime.strptime(expire_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
            rows.append({
                "室外機型號": model, "類別": category,
                "證書編號": cert_no, "有效期限": expire_date.isoformat(),
            })
        return rows, True, None
    except Exception as e:
        error_detail = f"{type(e).__name__}: {e}"
        # 讀不到就退回本地備用資料（一樣不去重）
        with open(os.path.join(HERE, "cert_data.json"), encoding="utf-8") as f:
            fallback = json.load(f)
        rows = [
            {"室外機型號": m, "類別": c.get("類別"), "證書編號": c.get("證書編號"), "有效期限": c.get("有效期限")}
            for m, c in fallback.items() if c.get("有效期限")
        ]
        return rows, False, error_detail

@st.cache_data(show_spinner=False)
def load_sales_data():
    with open(os.path.join(HERE, "sales_data.json"), encoding="utf-8") as f:
        return json.load(f)

productdept_rows, productdept_live, productdept_error = load_productdept_rows()
sales_records = load_sales_data()

sales_df = pd.DataFrame(sales_records)
sales_df["銷售量"] = pd.to_numeric(sales_df["銷售量"], errors="coerce").fillna(0)

today = date.today()

def build_cert_rows():
    rows = []
    for r in productdept_rows:
        if not r.get("有效期限"):
            continue
        expire_date = datetime.strptime(r["有效期限"], "%Y-%m-%d").date()
        days_left = (expire_date - today).days
        rows.append({
            "室外機型號": r["室外機型號"],
            "類別": r.get("類別"),
            "證書編號": r.get("證書編號"),
            "有效期限": expire_date,
            "剩餘天數": days_left,
        })
    return pd.DataFrame(rows)

cert_df = build_cert_rows()

def build_email_html(edited_df, threshold_label):
    def status_of(row):
        if row["要展延"]:
            return "✅ 要展延"
        if row["不展延"]:
            return "❌ 不展延"
        return "⚠️ 尚未決定"

    rows_html = ""
    for _, r in edited_df.iterrows():
        status = status_of(r)
        rows_html += f"""
        <tr>
          <td style="padding:6px 10px;border:1px solid #ddd">{r['室外機型號']}</td>
          <td style="padding:6px 10px;border:1px solid #ddd">{r['類別'] or ''}</td>
          <td style="padding:6px 10px;border:1px solid #ddd">{r['證書編號'] or ''}</td>
          <td style="padding:6px 10px;border:1px solid #ddd">{r['有效期限']}</td>
          <td style="padding:6px 10px;border:1px solid #ddd;text-align:center">{r['剩餘天數']}</td>
          <td style="padding:6px 10px;border:1px solid #ddd">{status}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:'Microsoft JhengHei',Arial,sans-serif;color:#222">
      <h3>【證書展延決策通知】{today.strftime('%Y/%m/%d')}</h3>
      <p>門檻：{threshold_label}內到期　總筆數：{len(edited_df)}</p>
      <table style="border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="background:#1a3f6f;color:white">
            <th style="padding:6px 10px;border:1px solid #ddd">室外機型號</th>
            <th style="padding:6px 10px;border:1px solid #ddd">類別</th>
            <th style="padding:6px 10px;border:1px solid #ddd">證書編號</th>
            <th style="padding:6px 10px;border:1px solid #ddd">有效期限</th>
            <th style="padding:6px 10px;border:1px solid #ddd">剩餘天數</th>
            <th style="padding:6px 10px;border:1px solid #ddd">決策狀態</th>
          </tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
      <p style="color:#888;font-size:12px;margin-top:16px">此為系統自動發送，請勿回覆。</p>
    </body></html>"""
    return html

def send_mail(recipients, subject, html_body):
    gmail_user = st.secrets["GMAIL_ADDRESS"]
    gmail_pass = st.secrets["GMAIL_APP_PASSWORD"]
    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, recipients, msg.as_string())

# ─────────────────────────────────────────────
# 標題
# ─────────────────────────────────────────────
st.markdown("##### 📊 商品生命週期儀表板")
if productdept_live:
    st.caption(f"✅ 到期清單即時讀取自 ProductDept Google Sheets（{len(productdept_rows)} 筆，未去重）。銷售資料目前仍為上傳檔案做的原型資料。")
else:
    st.caption(f"⚠️ 尚未連上 ProductDept Google Sheets，暫時顯示本地備用資料。")
    st.code(productdept_error or "（沒有取得詳細錯誤訊息）", language=None)
    st.caption("需要在 Streamlit Cloud 的 Secrets 加入 `gcp_service_account` 服務帳號設定，並確認該帳號有這份 Google Sheets 的檢視權限。")

if "renewal_decisions" not in st.session_state:
    st.session_state["renewal_decisions"] = {}
if "search_threshold_days" not in st.session_state:
    st.session_state["search_threshold_days"] = 365
    st.session_state["search_threshold_label"] = "1年"

# ─────────────────────────────────────────────
# 到期清單（搜尋列：年/月 + 搜尋鈕，按下才更新）
# ─────────────────────────────────────────────
st.markdown("#### 📅 到期清單")

s1, s2, s3 = st.columns([0.7, 0.7, 1])
with s1:
    year_part = st.selectbox("年", list(range(0, 6)), index=1, key="expiry_year")
with s2:
    month_part = st.selectbox("月", list(range(0, 12)), index=0, key="expiry_month")
with s3:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    search_clicked = st.button("🔍 搜尋", use_container_width=True)

if search_clicked:
    st.session_state["search_threshold_days"] = year_part * 365 + month_part * 30
    st.session_state["search_threshold_label"] = "、".join(
        filter(None, [f"{year_part}年" if year_part else "", f"{month_part}個月" if month_part else ""])
    ) or "0天"

threshold_days = st.session_state["search_threshold_days"]
threshold_label = st.session_state["search_threshold_label"]

expiry_view = cert_df[(cert_df["剩餘天數"] >= 0) & (cert_df["剩餘天數"] <= threshold_days)].copy()
expiry_view = expiry_view.sort_values("剩餘天數")
st.caption(f"目前顯示：{threshold_label}內到期（約 {threshold_days} 天），共 {len(expiry_view)} 筆"
           "（室外機型號、證書編號皆不去重；同一張證書編號涵蓋多個型號時，展延勾選會連動）")

# 展延決策用「證書編號」分組：同一張證書底下的所有型號，勾選狀態一起連動
decisions = st.session_state["renewal_decisions"]
expiry_view["要展延"] = expiry_view["證書編號"].map(lambda k: decisions.get(k, {}).get("要展延", False))
expiry_view["不展延"] = expiry_view["證書編號"].map(lambda k: decisions.get(k, {}).get("不展延", False))

edited_expiry = st.data_editor(
    expiry_view[["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數", "要展延", "不展延"]],
    use_container_width=True,
    hide_index=True,
    disabled=["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數"],
    column_config={
        "要展延": st.column_config.CheckboxColumn("要展延"),
        "不展延": st.column_config.CheckboxColumn("不展延"),
    },
    key="expiry_editor",
)

# 找出這次編輯中「哪一個證書編號的勾選狀態有變動」，把同證書編號的所有列都同步更新
changed_certs = {}
for _, row in edited_expiry.iterrows():
    cert = row["證書編號"]
    prev = decisions.get(cert, {"要展延": False, "不展延": False})
    now = {"要展延": bool(row["要展延"]), "不展延": bool(row["不展延"])}
    if now != prev:
        changed_certs[cert] = now

for cert, val in changed_certs.items():
    st.session_state["renewal_decisions"][cert] = val
if changed_certs:
    st.rerun()

# 沒有變動的部分，把目前狀態存回去（確保新出現的證書編號也有預設值）
for _, row in edited_expiry.iterrows():
    cert = row["證書編號"]
    if cert not in st.session_state["renewal_decisions"]:
        st.session_state["renewal_decisions"][cert] = {
            "要展延": bool(row["要展延"]),
            "不展延": bool(row["不展延"]),
        }

st.divider()

# ─────────────────────────────────────────────
# 手動寄送（立即寄出目前清單）
# ─────────────────────────────────────────────
st.markdown("**手動寄送展延決策通知**")
mail_col1, mail_col2 = st.columns([2, 1])
with mail_col1:
    recipient_input = st.text_input("收件信箱（多個用逗號分隔）", placeholder="example1@company.com, example2@company.com")
with mail_col2:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    send_clicked = st.button("📨 立即寄送", use_container_width=True)

if send_clicked:
    recipients = [r.strip() for r in recipient_input.split(",") if r.strip()]
    if not recipients:
        st.error("請輸入至少一個收件信箱")
    else:
        body = build_email_html(edited_expiry, threshold_label)
        try:
            send_mail(recipients, f"【證書展延決策通知】{today.strftime('%Y/%m/%d')}", body)
            st.success(f"已寄出通知信給 {len(recipients)} 位收件人")
        except KeyError:
            st.error("⚠️ 尚未設定寄信帳號，請在 Streamlit Cloud 的 Secrets 加入 GMAIL_ADDRESS 與 GMAIL_APP_PASSWORD")
        except Exception as e:
            st.error(f"寄信失敗：{e}")

st.divider()

MAILSCHEDULE_TAB_NAME = "MailSchedule"

def _get_gspread_client(readonly=True):
    import gspread
    from google.oauth2.service_account import Credentials
    sa_info = dict(st.secrets["gcp_service_account"])
    scope = "spreadsheets.readonly" if readonly else "spreadsheets"
    creds = Credentials.from_service_account_info(
        sa_info, scopes=[f"https://www.googleapis.com/auth/{scope}"]
    )
    return gspread.authorize(creds)

def load_schedule_rows():
    """從 Total Certificate Management 底下的 MailSchedule 分頁讀取排程設定，
    分頁不存在的話就回傳預設值（1/10、7/10、1年3個月）。"""
    try:
        gc = _get_gspread_client(readonly=True)
        sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
        ws = sh.worksheet(MAILSCHEDULE_TAB_NAME)
        values = ws.get_all_values()
        rows = []
        for row in values[1:]:
            if len(row) < 5 or not row[0].strip():
                continue
            rows.append({
                "月": int(row[0]), "日": int(row[1]),
                "年門檻": int(row[2]), "月門檻": int(row[3]),
                "收件信箱": row[4].strip(),
            })
        return rows if rows else None
    except Exception:
        return None

def save_schedule_rows(rows):
    """把排程設定寫進 MailSchedule 分頁；分頁不存在就自動建立一個。"""
    gc = _get_gspread_client(readonly=False)
    sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
    try:
        ws = sh.worksheet(MAILSCHEDULE_TAB_NAME)
    except Exception:
        ws = sh.add_worksheet(title=MAILSCHEDULE_TAB_NAME, rows=20, cols=5)
    ws.clear()
    header = ["月", "日", "年門檻", "月門檻", "收件信箱"]
    data = [header] + [[r["月"], r["日"], r["年門檻"], r["月門檻"], r["收件信箱"]] for r in rows]
    ws.update(data)

# ─────────────────────────────────────────────
# 定時寄信設定（兩列，日期／到期範圍都可調整；
# 這裡只是「設定」介面，實際自動觸發需要另外做 Lambda + EventBridge 排程）
# ─────────────────────────────────────────────
st.markdown("**⏰ 定時寄信設定**")
st.caption("這裡設定的內容會寫進 Total Certificate Management 底下的 MailSchedule 分頁，"
           "之後排程用的 Lambda 會讀取同一份設定，兩邊不會對不上。")

if "schedule_rows" not in st.session_state:
    loaded = load_schedule_rows()
    st.session_state["schedule_rows"] = loaded or [
        {"月": 1, "日": 10, "年門檻": 1, "月門檻": 3, "收件信箱": ""},
        {"月": 7, "日": 10, "年門檻": 1, "月門檻": 3, "收件信箱": ""},
    ]

schedule_df = pd.DataFrame(st.session_state["schedule_rows"])
edited_schedule = st.data_editor(
    schedule_df,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
    column_config={
        "月": st.column_config.SelectboxColumn("觸發月", options=list(range(1, 13)), required=True),
        "日": st.column_config.SelectboxColumn("觸發日", options=list(range(1, 32)), required=True),
        "年門檻": st.column_config.SelectboxColumn("到期範圍－年", options=list(range(0, 6)), required=True),
        "月門檻": st.column_config.SelectboxColumn("到期範圍－月", options=list(range(0, 12)), required=True),
        "收件信箱": st.column_config.TextColumn("收件信箱（逗號分隔）"),
    },
    key="schedule_editor",
)
st.session_state["schedule_rows"] = edited_schedule.to_dict("records")

if st.button("💾 儲存排程設定"):
    try:
        save_schedule_rows(st.session_state["schedule_rows"])
        st.success("排程設定已寫入 Google Sheets 的 MailSchedule 分頁。")
    except Exception as e:
        st.error(f"儲存失敗：{e}")
