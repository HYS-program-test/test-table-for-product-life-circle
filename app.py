import streamlit as st
import pandas as pd
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime

st.set_page_config(page_title="商品生命週期與銷售儀表板", page_icon="📊", layout="wide")

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTDEPT_SHEET_ID = "1dL7OxhYKpqaVnYn-IsOlqpQq-bREwWoo"

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
        # 實際欄位：A=實驗室、B=類別、C=室外機型號、D=測試搭配(室內機)、E=證書編號、F=有效期限
        # 畫面上的「Table_1」是 Google Sheets 表格功能的名稱標籤，不是資料列，
        # 所以第1列就是欄位標題，資料從第2列開始。
        for row in values[1:]:
            if len(row) < 6 or not row[2].strip():
                continue
            category, model, cert_no, expire_str = row[1].strip(), row[2].strip(), row[4].strip(), row[5].strip()
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
        return rows, True
    except Exception as e:
        st.session_state["_productdept_error"] = str(e)
        # 讀不到就退回本地備用資料（一樣不去重）
        with open(os.path.join(HERE, "cert_data.json"), encoding="utf-8") as f:
            fallback = json.load(f)
        rows = [
            {"室外機型號": m, "類別": c.get("類別"), "證書編號": c.get("證書編號"), "有效期限": c.get("有效期限")}
            for m, c in fallback.items() if c.get("有效期限")
        ]
        return rows, False

@st.cache_data(show_spinner=False)
def load_sales_data():
    with open(os.path.join(HERE, "sales_data.json"), encoding="utf-8") as f:
        return json.load(f)

productdept_rows, productdept_live = load_productdept_rows()
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
st.markdown("##### 📊 商品生命週期與銷售數量儀表板")
if productdept_live:
    st.caption(f"✅ 到期清單即時讀取自 ProductDept Google Sheets（{len(productdept_rows)} 筆，未去重）。銷售資料目前仍為上傳檔案做的原型資料。")
else:
    err = st.session_state.get("_productdept_error", "")
    st.caption(f"⚠️ 尚未連上 ProductDept Google Sheets，暫時顯示本地備用資料。錯誤訊息：{err}")
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
st.caption(f"目前顯示：{threshold_label}內到期（約 {threshold_days} 天），共 {len(expiry_view)} 筆")

# 用「室外機型號＋證書編號」當作每一列的唯一識別，避免同型號有多筆證書紀錄時
# 互相覆蓋勾選狀態、或看起來像資料被去重掉了
expiry_view["_row_key"] = expiry_view["室外機型號"].astype(str) + "｜" + expiry_view["證書編號"].astype(str)

decisions = st.session_state["renewal_decisions"]
expiry_view["要展延"] = expiry_view["_row_key"].map(lambda k: decisions.get(k, {}).get("要展延", False))
expiry_view["不展延"] = expiry_view["_row_key"].map(lambda k: decisions.get(k, {}).get("不展延", False))

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

# 存回決策狀態時，一樣用「室外機型號＋證書編號」當 key
edited_expiry["_row_key"] = edited_expiry["室外機型號"].astype(str) + "｜" + edited_expiry["證書編號"].astype(str)
for _, row in edited_expiry.iterrows():
    st.session_state["renewal_decisions"][row["_row_key"]] = {
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

# ─────────────────────────────────────────────
# 定時寄信設定（兩列，日期／到期範圍都可調整；
# 這裡只是「設定」介面，實際自動觸發需要另外做 Lambda + EventBridge 排程）
# ─────────────────────────────────────────────
st.markdown("**⏰ 定時寄信設定**")
st.caption("這裡只負責設定「什麼時候寄、寄多久內到期的清單」，實際「自動在那天寄出」需要另外做一個 "
           "Lambda 排程（跟你們原本 cert-expiry-notifier 同一種做法），設定確認後我再協助串接。")

if "schedule_rows" not in st.session_state:
    st.session_state["schedule_rows"] = [
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
        "日": st.column_config.SelectboxColumn("觸發日", options=list(range(1, 29)), required=True),
        "年門檻": st.column_config.SelectboxColumn("到期範圍－年", options=list(range(0, 6)), required=True),
        "月門檻": st.column_config.SelectboxColumn("到期範圍－月", options=list(range(0, 12)), required=True),
        "收件信箱": st.column_config.TextColumn("收件信箱（逗號分隔）"),
    },
    key="schedule_editor",
)
st.session_state["schedule_rows"] = edited_schedule.to_dict("records")

if st.button("💾 儲存排程設定"):
    st.success("排程設定已暫存（目前僅存在這次瀏覽的畫面狀態；確定內容沒問題後，"
               "我會協助把這份設定寫進 Lambda，讓它每年準時自動執行）。")
