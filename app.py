import streamlit as st

st.set_page_config(page_title="商品生命週期管理", page_icon="📦", layout="wide")

import pandas as pd
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTDEPT_SHEET_ID = "1hEt4uxBABBicxIMJuR57lMiigQYF02CQHZfB-Nc6vjo"  # Total Certificate Management

DECISIONS_TAB = "RenewalDecisions"   # 展延/不展延 勾選狀態，跟外部獨立網站共用同一份，達成同步
PENDING_TAB = "RenewalPending"       # 待展延清單（年費/規費等可手動填的欄位）
HISTORY_TAB = "RenewalHistory"       # 歷史展延清單（每次送出都累加一份紀錄，不覆蓋）

PENDING_COLUMNS = ["室外機型號", "類別", "證書編號", "有效期限", "年費", "規費", "空白1", "空白2"]
HISTORY_COLUMNS = ["送出時間", "室外機型號", "類別", "證書編號", "有效期限", "決策狀態"]


# ─────────────────────────────────────────────
# Google Sheets 存取
# ─────────────────────────────────────────────
def _get_gspread_client(readonly=True):
    import gspread
    from google.oauth2.service_account import Credentials
    sa_info = dict(st.secrets["gcp_service_account"])
    scope = "spreadsheets.readonly" if readonly else "spreadsheets"
    creds = Credentials.from_service_account_info(
        sa_info, scopes=[f"https://www.googleapis.com/auth/{scope}"]
    )
    return gspread.authorize(creds)


def _get_or_create_ws(sh, title, rows=200, cols=10):
    try:
        return sh.worksheet(title)
    except Exception:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


@st.cache_data(ttl=300, show_spinner=False)
def load_productdept_rows():
    """從 ProductDept 這份 Google Sheets 即時讀取到期清單，不做任何去重。
    如果還沒設定 Google 服務帳號，就退回讀本地備用的 JSON。"""
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


@st.cache_data(ttl=30, show_spinner=False)
def load_decisions_from_sheet():
    """讀取展延/不展延勾選狀態，跟外部獨立網站共用同一份分頁，達成跨部署同步。"""
    try:
        gc = _get_gspread_client(readonly=True)
        sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
        ws = sh.worksheet(DECISIONS_TAB)
        values = ws.get_all_values()
        decisions = {}
        for row in values[1:]:
            if len(row) < 3 or not row[0].strip():
                continue
            decisions[row[0].strip()] = {
                "要展延": row[1].strip() == "TRUE",
                "不展延": row[2].strip() == "TRUE",
            }
        return decisions
    except Exception:
        return {}


def save_decisions_to_sheet(decisions: dict):
    """把目前的勾選狀態整份寫回 Google Sheets（清空重寫）。"""
    gc = _get_gspread_client(readonly=False)
    sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
    ws = _get_or_create_ws(sh, DECISIONS_TAB, rows=500, cols=3)
    ws.clear()
    header = ["證書編號", "要展延", "不展延"]
    data = [header] + [
        [cert, "TRUE" if v.get("要展延") else "FALSE", "TRUE" if v.get("不展延") else "FALSE"]
        for cert, v in decisions.items()
    ]
    ws.update(data)
    load_decisions_from_sheet.clear()


@st.cache_data(ttl=30, show_spinner=False)
def load_pending_from_sheet():
    try:
        gc = _get_gspread_client(readonly=True)
        sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
        ws = sh.worksheet(PENDING_TAB)
        values = ws.get_all_values()
        if len(values) < 2:
            return pd.DataFrame(columns=PENDING_COLUMNS)
        return pd.DataFrame(values[1:], columns=values[0])
    except Exception:
        return pd.DataFrame(columns=PENDING_COLUMNS)


def save_pending_to_sheet(df: pd.DataFrame):
    gc = _get_gspread_client(readonly=False)
    sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
    ws = _get_or_create_ws(sh, PENDING_TAB, rows=500, cols=10)
    ws.clear()
    data = [list(df.columns)] + df.astype(str).values.tolist()
    ws.update(data)
    load_pending_from_sheet.clear()


@st.cache_data(ttl=30, show_spinner=False)
def load_history_from_sheet():
    try:
        gc = _get_gspread_client(readonly=True)
        sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
        ws = sh.worksheet(HISTORY_TAB)
        values = ws.get_all_values()
        if len(values) < 2:
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        return pd.DataFrame(values[1:], columns=values[0])
    except Exception:
        return pd.DataFrame(columns=HISTORY_COLUMNS)


def append_to_history(rows_df: pd.DataFrame):
    gc = _get_gspread_client(readonly=False)
    sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
    ws = _get_or_create_ws(sh, HISTORY_TAB, rows=2000, cols=10)
    existing = ws.get_all_values()
    if not existing:
        ws.append_row(HISTORY_COLUMNS)
    ws.append_rows(rows_df.astype(str).values.tolist())
    load_history_from_sheet.clear()


def _load_schedule_rows():
    try:
        gc = _get_gspread_client(readonly=True)
        sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
        ws = sh.worksheet("MailSchedule")
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


def _save_schedule_rows(rows):
    gc = _get_gspread_client(readonly=False)
    sh = gc.open_by_key(PRODUCTDEPT_SHEET_ID)
    ws = _get_or_create_ws(sh, "MailSchedule", rows=20, cols=5)
    ws.clear()
    header = ["月", "日", "年門檻", "月門檻", "收件信箱"]
    data = [header] + [[r["月"], r["日"], r["年門檻"], r["月門檻"], r["收件信箱"]] for r in rows]
    ws.update(data)


def render():
    st.markdown("### 📦 商品生命週期管理")
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
                "室外機型號": r["室外機型號"], "類別": r.get("類別"),
                "證書編號": r.get("證書編號"), "有效期限": expire_date, "剩餘天數": days_left,
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
        return f"""
        <html><body style="font-family:'Microsoft JhengHei',Arial,sans-serif;color:#222">
          <h3>【證書展延決策通知】{today.strftime('%Y/%m/%d')}</h3>
          <p>門檻：{threshold_label}內到期　總筆數：{len(edited_df)}</p>
          <table style="border-collapse:collapse;font-size:14px">
            <thead><tr style="background:#1a3f6f;color:white">
              <th style="padding:6px 10px;border:1px solid #ddd">室外機型號</th>
              <th style="padding:6px 10px;border:1px solid #ddd">類別</th>
              <th style="padding:6px 10px;border:1px solid #ddd">證書編號</th>
              <th style="padding:6px 10px;border:1px solid #ddd">有效期限</th>
              <th style="padding:6px 10px;border:1px solid #ddd">剩餘天數</th>
              <th style="padding:6px 10px;border:1px solid #ddd">決策狀態</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
          <p style="color:#888;font-size:12px;margin-top:16px">此為系統自動發送，請勿回覆。</p>
        </body></html>"""

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

    if productdept_live:
        st.caption(f"✅ 到期清單即時讀取自 ProductDept Google Sheets（{len(productdept_rows)} 筆，未去重）。")
    else:
        st.caption("⚠️ 尚未連上 ProductDept Google Sheets，暫時顯示本地備用資料。")
        st.code(productdept_error or "（沒有取得詳細錯誤訊息）", language=None)

    if "renewal_decisions" not in st.session_state or not st.session_state.get("_decisions_loaded_once"):
        st.session_state["renewal_decisions"] = load_decisions_from_sheet()
        st.session_state["_decisions_loaded_once"] = True
    if "search_threshold_days" not in st.session_state:
        st.session_state["search_threshold_days"] = 365
        st.session_state["search_threshold_label"] = "1年"

    # ══════════════════════════════════════════════
    # 面板 1：商品生命週期
    # ══════════════════════════════════════════════
    with st.expander("📅　商品生命週期", expanded=True):
        col_refresh, _ = st.columns([1, 4])
        with col_refresh:
            if st.button("🔄 同步最新決策狀態", use_container_width=True):
                load_decisions_from_sheet.clear()
                st.session_state["renewal_decisions"] = load_decisions_from_sheet()
                st.rerun()

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

        decisions = st.session_state["renewal_decisions"]
        expiry_view["要展延"] = expiry_view["證書編號"].map(lambda k: decisions.get(k, {}).get("要展延", False))
        expiry_view["不展延"] = expiry_view["證書編號"].map(lambda k: decisions.get(k, {}).get("不展延", False))

        edited_expiry = st.data_editor(
            expiry_view[["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數", "要展延", "不展延"]],
            use_container_width=True, hide_index=True,
            disabled=["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數"],
            column_config={
                "要展延": st.column_config.CheckboxColumn("要展延"),
                "不展延": st.column_config.CheckboxColumn("不展延"),
            },
            key="expiry_editor",
        )

        changed_certs = {}
        for _, row in edited_expiry.iterrows():
            cert = row["證書編號"]
            prev = decisions.get(cert, {"要展延": False, "不展延": False})
            now = {"要展延": bool(row["要展延"]), "不展延": bool(row["不展延"])}
            if now != prev:
                changed_certs[cert] = now

        if changed_certs:
            for cert, val in changed_certs.items():
                st.session_state["renewal_decisions"][cert] = val
            try:
                save_decisions_to_sheet(st.session_state["renewal_decisions"])
            except Exception as e:
                st.warning(f"勾選已更新在畫面上，但寫回 Google Sheets 失敗（外部網站可能看不到這次變動）：{e}")
            st.rerun()

        st.divider()
        st.markdown("**手動寄送展延決策通知**")
        mail_col1, mail_col2 = st.columns([2, 1])
        with mail_col1:
            recipient_input = st.text_input("收件信箱（多個用逗號分隔）", placeholder="example1@company.com, example2@company.com")
        with mail_col2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            send_clicked = st.button("📨 立即寄送並送出決策", use_container_width=True)

        if send_clicked:
            recipients = [r.strip() for r in recipient_input.split(",") if r.strip()]
            if not recipients:
                st.error("請輸入至少一個收件信箱")
            else:
                body = build_email_html(edited_expiry, threshold_label)
                sent_ok = False
                try:
                    send_mail(recipients, f"【證書展延決策通知】{today.strftime('%Y/%m/%d')}", body)
                    st.success(f"已寄出通知信給 {len(recipients)} 位收件人")
                    sent_ok = True
                except KeyError:
                    st.error("⚠️ 尚未設定寄信帳號，請在 Streamlit Cloud 的 Secrets 加入 GMAIL_ADDRESS 與 GMAIL_APP_PASSWORD")
                except Exception as e:
                    st.error(f"寄信失敗：{e}")

                if sent_ok:
                    to_renew = edited_expiry[edited_expiry["要展延"]].copy()
                    if not to_renew.empty:
                        pending_df = load_pending_from_sheet()
                        existing_certs = set(pending_df["證書編號"]) if not pending_df.empty else set()
                        new_rows = []
                        for _, r in to_renew.iterrows():
                            if r["證書編號"] in existing_certs:
                                continue
                            new_rows.append({
                                "室外機型號": r["室外機型號"], "類別": r["類別"],
                                "證書編號": r["證書編號"], "有效期限": str(r["有效期限"]),
                                "年費": "", "規費": "", "空白1": "", "空白2": "",
                            })
                        if new_rows:
                            pending_df = pd.concat([pending_df, pd.DataFrame(new_rows)], ignore_index=True)
                            try:
                                save_pending_to_sheet(pending_df)
                            except Exception as e:
                                st.warning(f"待展延清單寫入失敗：{e}")

                        history_rows = pd.DataFrame([{
                            "送出時間": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "室外機型號": r["室外機型號"], "類別": r["類別"],
                            "證書編號": r["證書編號"], "有效期限": str(r["有效期限"]),
                            "決策狀態": "要展延",
                        } for _, r in to_renew.iterrows()])
                        try:
                            append_to_history(history_rows)
                        except Exception as e:
                            st.warning(f"歷史展延清單寫入失敗：{e}")
                    st.success("已同步更新「待展延清單」與「歷史展延清單」，可展開下方面板查看。")

        st.divider()
        st.markdown("**⏰ 定時寄信設定**")
        st.caption("這裡設定的內容會寫進 Total Certificate Management 底下的 MailSchedule 分頁。")

        if "schedule_rows" not in st.session_state:
            loaded = _load_schedule_rows()
            st.session_state["schedule_rows"] = loaded or [
                {"月": 1, "日": 10, "年門檻": 1, "月門檻": 3, "收件信箱": ""},
                {"月": 7, "日": 10, "年門檻": 1, "月門檻": 3, "收件信箱": ""},
            ]

        schedule_df = pd.DataFrame(st.session_state["schedule_rows"])
        edited_schedule = st.data_editor(
            schedule_df, use_container_width=True, hide_index=True, num_rows="fixed",
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
                _save_schedule_rows(st.session_state["schedule_rows"])
                st.success("排程設定已寫入 Google Sheets 的 MailSchedule 分頁。")
            except Exception as e:
                st.error(f"儲存失敗：{e}")

    # ══════════════════════════════════════════════
    # 面板 2：待展延清單
    # ══════════════════════════════════════════════
    with st.expander("📋　待展延清單", expanded=False):
        pending_df = load_pending_from_sheet()
        if pending_df.empty:
            st.caption("目前沒有待展延資料——在上方「商品生命週期」勾選「要展延」並送出後，會自動加進這裡。")
        else:
            st.caption(f"共 {len(pending_df)} 筆，年費／規費／空白欄位可直接在表格內編輯。")
            edited_pending = st.data_editor(
                pending_df, use_container_width=True, hide_index=True,
                disabled=["室外機型號", "類別", "證書編號", "有效期限"],
                column_config={
                    "室外機型號": st.column_config.TextColumn("室外機型號", width="small"),
                    "類別": st.column_config.TextColumn("類別", width="small"),
                    "證書編號": st.column_config.TextColumn("證書編號", width="small"),
                    "有效期限": st.column_config.TextColumn("有效期限", width="small"),
                    "年費": st.column_config.TextColumn("年費", width="small"),
                    "規費": st.column_config.TextColumn("規費", width="small"),
                    "空白1": st.column_config.TextColumn("空白1", width="small"),
                    "空白2": st.column_config.TextColumn("空白2", width="small"),
                },
                key="pending_editor",
            )
            if st.button("💾 儲存待展延清單", key="save_pending_btn"):
                try:
                    save_pending_to_sheet(edited_pending)
                    st.success("已儲存。")
                except Exception as e:
                    st.error(f"儲存失敗：{e}")

    # ══════════════════════════════════════════════
    # 面板 3：歷史展延清單
    # ══════════════════════════════════════════════
    with st.expander("🗂️　歷史展延清單", expanded=False):
        history_df = load_history_from_sheet()
        if history_df.empty:
            st.caption("目前沒有歷史紀錄——每次在上方送出展延決策通知後，會自動在這裡累加一筆存檔。")
        else:
            st.caption(f"共 {len(history_df)} 筆歷史紀錄（依送出時間由新到舊）。")
            display_hist = history_df.iloc[::-1].reset_index(drop=True)
            st.dataframe(display_hist, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render()
