"""
DUTDIEMTV Support Bot
=====================
Bot Discord báo lỗi & hỗ trợ người dùng website DUTDIEMTV:

  * Bảng báo lỗi: stream không hoạt động, không vào được web, có tiếng không có hình,
    không có tiếng, giật/lag, lỗi khác.
  * Bot hướng dẫn tự khắc phục trước -> nếu vẫn lỗi mới gửi báo cáo (ticket) cho đội ngũ,
    đội ngũ bấm nút Nhận / Đã xử lý và người báo lỗi được nhắn lại qua DM.
  * Tự động giám sát website, báo vào kênh khi web sập / hoạt động trở lại.
  * Chào người mới, hướng dẫn nhanh, FAQ.
  * (Tuỳ chọn) Tự gợi ý cách khắc phục khi thành viên mô tả lỗi trong kênh hỗ trợ.

Chạy:
  pip install -r requirements.txt
  Đổi .env.example thành .env rồi điền thông tin
  python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dutdiemtv-bot")


# =====================================================================
#  CẤU HÌNH (đọc từ file .env)
# =====================================================================
def env_int(name: str) -> int:
    try:
        return int(os.getenv(name, "0") or 0)
    except ValueError:
        return 0


TOKEN = os.getenv("DISCORD_TOKEN", "MTU2Nzg5MzQ3MjAwNDkzMTcyNA.GsJYni.hUf8-CIbKqRU3glWaM2948RJXnWJABkfFc07bg")
GUILD_ID = env_int("GUILD_ID")                    # ID server (để lệnh / cập nhật tức thì). Bỏ trống = global
SITE_NAME = "DUTDIEMTV"
SITE_URL = os.getenv("SITE_URL", "https://example.com")   # <-- ĐỔI thành địa chỉ website của bạn
STREAM_CHECK_URL = os.getenv("STREAM_CHECK_URL", "")      # (tuỳ chọn) URL máy chủ stream để giám sát

STAFF_CHANNEL_ID = env_int("STAFF_CHANNEL_ID")    # Kênh nhận báo cáo lỗi (chỉ staff thấy)
ALERT_CHANNEL_ID = env_int("ALERT_CHANNEL_ID")    # Kênh thông báo web sập / hoạt động lại
WELCOME_CHANNEL_ID = env_int("WELCOME_CHANNEL_ID")  # Kênh chào thành viên mới
SUPPORT_CHANNEL_ID = env_int("SUPPORT_CHANNEL_ID")  # Kênh hỗ trợ (dùng cho tự gợi ý)
SUPPORT_ROLE_ID = env_int("SUPPORT_ROLE_ID")      # Role đội ngũ hỗ trợ (được ping & dùng nút xử lý)

CHECK_INTERVAL = max(30, env_int("CHECK_INTERVAL") or 120)   # giây giữa các lần kiểm tra web
FAIL_THRESHOLD = 2                                # số lần lỗi liên tiếp mới báo (tránh báo nhầm)
ENABLE_AUTOREPLY = os.getenv("ENABLE_AUTOREPLY", "false").lower() == "true"
WELCOME_DM = os.getenv("WELCOME_DM", "false").lower() == "true"

REPORT_COOLDOWN = 60       # giây giữa 2 lần báo lỗi của cùng 1 người
MAX_OPEN_REPORTS = 3       # số báo cáo đang mở tối đa / người
DB_PATH = "dutdiemtv_bot.db"
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DUTDIEMTV-SupportBot/1.0)"}

BRAND_COLOR = discord.Color.from_rgb(88, 101, 242)


# =====================================================================
#  NỘI DUNG  --  ✏️ HÃY CHỈNH LẠI CHO ĐÚNG VỚI WEBSITE CỦA BẠN
# =====================================================================
ERROR_TYPES: dict[str, dict] = {
    "stream_down": {
        "label": "Stream không hoạt động",
        "emoji": "📡",
        "desc": "Kênh/trận không phát, màn hình đen hoặc báo lỗi",
        "steps": [
            "Kiểm tra trận/kênh đã đến giờ phát chưa - nhiều nguồn chỉ mở sát giờ thi đấu.",
            "Tải lại trang bằng **Ctrl + F5** (điện thoại: kéo màn hình xuống để tải lại).",
            "Thử chọn **nguồn phát / server khác** nếu trang có nhiều lựa chọn.",
            "Thử trình duyệt khác (Chrome, Edge, Firefox) hoặc mở tab ẩn danh.",
            "Tạm tắt tiện ích chặn quảng cáo (AdBlock...) cho trang rồi tải lại.",
        ],
    },
    "site_down": {
        "label": "Không vào được website",
        "emoji": "🌐",
        "desc": "Trang không tải, báo lỗi kết nối hoặc quay mãi",
        "steps": [
            "Kiểm tra bạn vẫn vào được trang khác (Google, YouTube...) - nếu không, lỗi nằm ở mạng của bạn.",
            "Thử đổi mạng: **Wi-Fi ↔ 4G/5G**. Nếu mạng khác vào được thì lỗi do nhà mạng.",
            "Đổi DNS sang `1.1.1.1` hoặc `8.8.8.8`. Windows: mở CMD gõ `ipconfig /flushdns`.",
            "Xoá cache/cookie của trang hoặc mở tab ẩn danh.",
            "Chắc chắn bạn đang gõ đúng địa chỉ chính thức của website.",
        ],
    },
    "no_video": {
        "label": "Nghe được tiếng nhưng không thấy hình",
        "emoji": "🖼️",
        "desc": "Có âm thanh nhưng màn hình đen / đứng hình",
        "steps": [
            "Tải lại trang (**Ctrl + F5**) rồi bấm nút phát trên player một lần nữa.",
            "Tắt **tăng tốc phần cứng**: Chrome → Cài đặt → Hệ thống → tắt "
            "*Sử dụng tính năng tăng tốc phần cứng* → khởi động lại trình duyệt.",
            "Cập nhật trình duyệt và driver card đồ hoạ lên bản mới nhất.",
            "Tắt các extension (AdBlock, tải video, VPN...) hoặc dùng tab ẩn danh.",
            "Thử nguồn phát/chất lượng khác. Trên điện thoại hãy mở bằng Chrome/Safari "
            "thay vì trình duyệt tích hợp trong app (Zalo, Facebook...).",
        ],
    },
    "no_audio": {
        "label": "Có hình nhưng không có tiếng",
        "emoji": "🔇",
        "desc": "Xem được hình nhưng không nghe thấy gì",
        "steps": [
            "Kiểm tra tab không bị tắt tiếng (chuột phải vào tab → **Bật âm thanh**).",
            "Kiểm tra nút loa trên player và âm lượng hệ thống. iPhone: kiểm tra công tắc im lặng.",
            "Kiểm tra thiết bị phát âm thanh đã chọn đúng (loa / tai nghe / Bluetooth).",
            "Tải lại trang hoặc thử trình duyệt khác.",
        ],
    },
    "lag": {
        "label": "Giật, lag, buffering liên tục",
        "emoji": "🐢",
        "desc": "Hình bị đứng, quay vòng tròn, chậm",
        "steps": [
            "Hạ chất lượng video trong player (nếu có).",
            "Đóng bớt tab/ứng dụng đang tải nặng (torrent, cập nhật, game...).",
            "Dùng cáp LAN hoặc Wi-Fi 5GHz, đứng gần router; thử khởi động lại modem.",
            "Thử nguồn phát khác - lúc đông người xem có thể bị chậm tạm thời.",
        ],
    },
    "other": {
        "label": "Lỗi khác",
        "emoji": "🛠️",
        "desc": "Vấn đề không nằm trong các mục trên",
        "steps": [
            "Tải lại trang bằng **Ctrl + F5**.",
            "Thử trình duyệt khác hoặc tab ẩn danh.",
            "Chụp màn hình lỗi, ghi lại giờ xảy ra rồi gửi báo cáo để đội ngũ kiểm tra.",
        ],
    },
}

GUIDE_STEPS = [
    f"Truy cập website **{SITE_NAME}** bằng địa chỉ chính thức do server công bố.",
    "Chọn kênh / trận đấu bạn muốn xem trong danh sách.",
    "Bấm nút phát. Nếu có nhiều nguồn/server, hãy thử nguồn khác khi nguồn đầu bị lỗi.",
    "Dùng trình duyệt bản mới (Chrome, Edge, Firefox, Safari) để có trải nghiệm tốt nhất.",
    "Gặp lỗi? Dùng lệnh `/baoloi` hoặc bảng **Báo lỗi** để được hướng dẫn và gửi báo cáo.",
]

FAQ: dict[str, tuple[str, str]] = {
    "bat_dau": (
        "Mình mới vào, bắt đầu xem như thế nào?",
        "Dùng lệnh `/huongdan` để xem hướng dẫn từng bước dành cho người mới.",
    ),
    "chua_den_gio": (
        "Trận chưa bắt đầu thì có xem được không?",
        "Thường nguồn phát được bật gần giờ thi đấu. Hãy tải lại trang (Ctrl + F5) "
        "trước giờ bóng lăn khoảng 10-15 phút.",
    ),
    "trinh_duyet": (
        "Nên dùng trình duyệt nào?",
        "Chrome, Edge hoặc Firefox bản mới nhất trên máy tính; Chrome/Safari trên điện thoại. "
        "Hạn chế mở link trong trình duyệt tích hợp của Zalo/Facebook.",
    ),
    "giat_lag": (
        "Xem bị giật/lag thì sao?",
        "Hạ chất lượng video, đóng bớt tab/ứng dụng nặng, dùng Wi-Fi 5GHz hoặc cáp mạng, "
        "hoặc thử nguồn phát khác. Chi tiết xem `/baoloi` → *Giật, lag*.",
    ),
    "bao_loi": (
        "Báo lỗi ở đâu?",
        "Dùng lệnh `/baoloi` hoặc chọn vấn đề ở bảng **Báo lỗi** trong kênh hỗ trợ. "
        "Bot sẽ gợi ý cách tự khắc phục trước, nếu vẫn lỗi bạn có thể gửi báo cáo cho đội ngũ.",
    ),
    "trang_thai": (
        "Làm sao biết web đang sập hay chỉ mình bị lỗi?",
        "Dùng lệnh `/trangthai` để bot kiểm tra website ngay lúc này.",
    ),
}

# Từ khoá tự gợi ý (đã bỏ dấu, viết thường). Thứ tự quan trọng: kiểm tra từ trên xuống.
AUTO_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("no_video", re.compile(r"\b(?:khong|ko|k)\s+(?:thay|co|len)\s+hinh\b|man hinh den|chi co tieng")),
    ("no_audio", re.compile(r"\b(?:khong|ko|k)\s+(?:co|nghe|thay)\s+(?:tieng|am thanh)\b|mat tieng|mat am thanh")),
    ("site_down", re.compile(r"\b(?:khong|ko)\s+(?:vao|truy cap|mo)\s*(?:duoc|dc)\b|\bweb\s+(?:sap|die|chet|loi)\b|\bsap web\b")),
    ("stream_down", re.compile(
        r"\b(?:stream|kenh|tran|link)\b.{0,30}\b(?:loi|die|chet|khong chay|ko chay|khong phat|ko phat|"
        r"khong xem duoc|ko xem duoc|khong len)\b")),
    ("lag", re.compile(r"\b(?:lag|giat|buffering|buffer|dung hinh|quay quay)\b")),
]


# =====================================================================
#  TIỆN ÍCH
# =====================================================================
def normalize(text: str) -> str:
    """Bỏ dấu tiếng Việt + viết thường để so khớp từ khoá."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d")


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} giờ")
    if m:
        parts.append(f"{m} phút")
    if not parts:
        parts.append(f"{s} giây")
    return " ".join(parts)


def is_staff(user: discord.abc.User) -> bool:
    if not isinstance(user, discord.Member):
        return False
    perms = user.guild_permissions
    if perms.administrator or perms.manage_messages:
        return True
    return bool(SUPPORT_ROLE_ID and any(r.id == SUPPORT_ROLE_ID for r in user.roles))


# ---------- Database (SQLite) ----------
def db_exec(sql: str, params: tuple = ()) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def db_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchone()


def db_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def init_db() -> None:
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            error_key   TEXT    NOT NULL,
            target      TEXT,
            device      TEXT,
            isp         TEXT,
            detail      TEXT,
            status      TEXT    NOT NULL DEFAULT 'open',
            staff_id    INTEGER,
            message_id  INTEGER,
            channel_id  INTEGER,
            created_at  INTEGER NOT NULL
        )
        """
    )


# ---------- Kiểm tra website ----------
@dataclass
class CheckResult:
    ok: bool
    status: int | None = None
    ms: int | None = None
    error: str | None = None


async def check_url(url: str) -> CheckResult:
    """Coi là 'hoạt động' nếu máy chủ trả lời với mã < 500 (kể cả 403 của Cloudflare)."""
    start = time.perf_counter()
    try:
        async with bot.web.get(url, allow_redirects=True) as resp:
            ms = int((time.perf_counter() - start) * 1000)
            return CheckResult(ok=resp.status < 500, status=resp.status, ms=ms)
    except asyncio.TimeoutError:
        return CheckResult(ok=False, error="Hết thời gian chờ (timeout)")
    except aiohttp.ClientError as e:
        return CheckResult(ok=False, error=type(e).__name__)
    except Exception as e:  # noqa: BLE001
        return CheckResult(ok=False, error=type(e).__name__)


def monitor_targets() -> dict[str, str]:
    targets = {"Website": SITE_URL}
    if STREAM_CHECK_URL:
        targets["Máy chủ stream"] = STREAM_CHECK_URL
    return targets


async def get_channel(channel_id: int):
    if not channel_id:
        return None
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return ch


# =====================================================================
#  EMBED BUILDERS
# =====================================================================
def panel_embed() -> discord.Embed:
    e = discord.Embed(
        title=f"🆘 Trung tâm hỗ trợ {SITE_NAME}",
        description=(
            "Gặp sự cố khi xem? Chọn vấn đề của bạn ở menu bên dưới.\n"
            "Bot sẽ gợi ý cách **tự khắc phục** trước. Nếu vẫn lỗi, bạn có thể gửi báo cáo "
            "cho đội ngũ chỉ với vài thao tác."
        ),
        color=BRAND_COLOR,
    )
    e.add_field(
        name="Các vấn đề thường gặp",
        value="\n".join(f"{v['emoji']} {v['label']}" for v in ERROR_TYPES.values()),
        inline=False,
    )
    e.add_field(name="Người mới?", value="Bấm **🔰 Hướng dẫn người mới** bên dưới.", inline=False)
    return e


def guide_embed() -> discord.Embed:
    e = discord.Embed(
        title=f"🔰 Hướng dẫn cho người mới - {SITE_NAME}",
        description="\n".join(f"**{i}.** {s}" for i, s in enumerate(GUIDE_STEPS, 1)),
        color=discord.Color.green(),
    )
    e.set_footer(text="Dùng /faq để xem các câu hỏi thường gặp")
    return e


def faq_list_embed() -> discord.Embed:
    e = discord.Embed(title="❓ Câu hỏi thường gặp", color=BRAND_COLOR)
    for q, a in FAQ.values():
        e.add_field(name=q, value=a, inline=False)
    return e


def troubleshoot_embed(key: str) -> discord.Embed:
    info = ERROR_TYPES[key]
    e = discord.Embed(
        title=f"{info['emoji']} {info['label']}",
        description="Hãy thử lần lượt các bước sau:\n\n"
        + "\n".join(f"**{i}.** {s}" for i, s in enumerate(info["steps"], 1)),
        color=discord.Color.orange(),
    )
    e.set_footer(text="Thử xong mà vẫn lỗi? Bấm nút “Vẫn lỗi” bên dưới để gửi báo cáo.")
    return e


async def status_embed() -> discord.Embed:
    targets = monitor_targets()
    results = await asyncio.gather(*(check_url(u) for u in targets.values()))
    all_ok = all(r.ok for r in results)
    e = discord.Embed(
        title="🩺 Trạng thái hệ thống",
        color=discord.Color.green() if all_ok else discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    for name, r in zip(targets.keys(), results):
        if r.ok:
            value = f"🟢 Đang hoạt động • HTTP {r.status} • {r.ms} ms"
        else:
            reason = r.error or f"HTTP {r.status}"
            value = f"🔴 Không phản hồi • {reason}"
        e.add_field(name=name, value=value, inline=False)
    if all_ok:
        e.set_footer(text="Máy chủ ổn. Nếu bạn vẫn lỗi, nhiều khả năng do mạng/thiết bị - dùng /baoloi")
    else:
        e.set_footer(text="Sự cố đã được ghi nhận, đội ngũ sẽ xử lý sớm nhất có thể.")
    return e


STATUS_INFO = {
    "open": ("🔴 Mới", discord.Color.red()),
    "claimed": ("🟡 Đang xử lý", discord.Color.gold()),
    "resolved": ("🟢 Đã xử lý", discord.Color.green()),
    "norepro": ("⚪ Không tái hiện được", discord.Color.light_grey()),
}


def report_embed(row: sqlite3.Row) -> discord.Embed:
    info = ERROR_TYPES.get(row["error_key"], ERROR_TYPES["other"])
    status_text, color = STATUS_INFO.get(row["status"], STATUS_INFO["open"])
    e = discord.Embed(
        title=f"{info['emoji']} Báo cáo #{row['id']} - {info['label']}",
        color=color,
        timestamp=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
    )
    e.add_field(name="Người báo", value=f"<@{row['user_id']}>", inline=True)
    e.add_field(name="Trạng thái", value=status_text, inline=True)
    e.add_field(name="Người xử lý", value=f"<@{row['staff_id']}>" if row["staff_id"] else "-", inline=True)
    e.add_field(name="Kênh / trận", value=row["target"] or "-", inline=False)
    e.add_field(name="Thiết bị & trình duyệt", value=row["device"] or "-", inline=True)
    e.add_field(name="Nhà mạng", value=row["isp"] or "-", inline=True)
    e.add_field(name="Mô tả", value=row["detail"] or "-", inline=False)
    return e


async def notify_reporter(row: sqlite3.Row) -> None:
    """Nhắn DM cho người báo lỗi khi báo cáo được xử lý."""
    if row["status"] == "resolved":
        title, desc, color = (
            f"✅ Báo cáo #{row['id']} đã được xử lý",
            "Cảm ơn bạn đã báo lỗi! Hãy tải lại trang (Ctrl + F5) và thử lại. "
            "Nếu vẫn gặp vấn đề, bạn có thể gửi báo cáo mới.",
            discord.Color.green(),
        )
    else:
        title, desc, color = (
            f"⚪ Báo cáo #{row['id']}: chưa tái hiện được lỗi",
            "Đội ngũ chưa thể tái hiện lỗi của bạn. Bạn hãy gửi báo cáo mới kèm **giờ xảy ra lỗi, "
            "ảnh chụp màn hình** và thiết bị/trình duyệt cụ thể để được hỗ trợ tốt hơn.",
            discord.Color.light_grey(),
        )
    try:
        user = await bot.fetch_user(row["user_id"])
        await user.send(embed=discord.Embed(title=title, description=desc, color=color))
    except discord.HTTPException:
        pass  # người dùng đóng DM


# =====================================================================
#  UI: BẢNG HỖ TRỢ (persistent)
# =====================================================================
async def send_troubleshoot(interaction: discord.Interaction, key: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    embed = troubleshoot_embed(key)

    # Với lỗi liên quan tới web/stream: kiểm tra nhanh xem máy chủ có đang sống không
    if key in ("site_down", "stream_down"):
        url = (STREAM_CHECK_URL or SITE_URL) if key == "stream_down" else SITE_URL
        result = await check_url(url)
        if result.ok:
            embed.add_field(
                name="✅ Máy chủ đang phản hồi bình thường",
                value=f"Bot vừa kiểm tra: HTTP {result.status}, {result.ms} ms. "
                      "Nhiều khả năng vấn đề nằm ở mạng/thiết bị của bạn - hãy thử các bước trên.",
                inline=False,
            )
        else:
            embed.add_field(
                name="⚠️ Bot phát hiện sự cố phía máy chủ",
                value="Vừa kiểm tra thì website **không phản hồi**. Hệ thống giám sát sẽ báo cho đội ngũ. "
                      "Bạn vẫn có thể gửi báo cáo bên dưới nếu muốn.",
                inline=False,
            )
    await interaction.followup.send(embed=embed, view=TroubleshootView(key), ephemeral=True)


class ErrorSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"], description=v["desc"][:100])
            for k, v in ERROR_TYPES.items()
        ]
        super().__init__(
            custom_id="dutdiem:error_select",
            placeholder="Chọn vấn đề bạn đang gặp...",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await send_troubleshoot(interaction, self.values[0])


class PanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(ErrorSelect())

    @discord.ui.button(label="Hướng dẫn người mới", emoji="🔰", style=discord.ButtonStyle.primary,
                       custom_id="dutdiem:guide", row=1)
    async def guide_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=guide_embed(), ephemeral=True)

    @discord.ui.button(label="Kiểm tra trạng thái", emoji="🩺", style=discord.ButtonStyle.secondary,
                       custom_id="dutdiem:status", row=1)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(embed=await status_embed(), ephemeral=True)

    @discord.ui.button(label="FAQ", emoji="❓", style=discord.ButtonStyle.secondary,
                       custom_id="dutdiem:faq", row=1)
    async def faq_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=faq_list_embed(), ephemeral=True)


class TroubleshootView(discord.ui.View):
    """Hiện sau khi người dùng chọn loại lỗi: 'Đã khắc phục' hoặc 'Vẫn lỗi'."""

    def __init__(self, key: str) -> None:
        super().__init__(timeout=900)
        self.key = key

    @discord.ui.button(label="Đã khắc phục", emoji="✅", style=discord.ButtonStyle.success)
    async def fixed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="🎉 Tuyệt vời, vậy là xong rồi! Chúc bạn xem vui vẻ.", embed=None, view=None
        )

    @discord.ui.button(label="Vẫn lỗi - Gửi báo cáo", emoji="📨", style=discord.ButtonStyle.danger)
    async def still_broken(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ReportModal(self.key))


class ReportModal(discord.ui.Modal):
    target = discord.ui.TextInput(
        label="Kênh / trận đang xem (hoặc dán link)",
        placeholder="VD: Trận Man City vs Arsenal - Nguồn 2",
        max_length=200,
    )
    device = discord.ui.TextInput(
        label="Thiết bị & trình duyệt",
        placeholder="VD: Windows 11 - Chrome 130 / iPhone 14 - Safari",
        max_length=100,
    )
    isp = discord.ui.TextInput(
        label="Nhà mạng (không bắt buộc)",
        placeholder="VD: VNPT, Viettel, FPT, Mobifone...",
        required=False,
        max_length=50,
    )
    detail = discord.ui.TextInput(
        label="Mô tả chi tiết",
        style=discord.TextStyle.paragraph,
        placeholder="Lỗi xảy ra từ lúc nào? Thấy thông báo gì? Đã thử những cách nào rồi?",
        max_length=1000,
    )

    def __init__(self, key: str) -> None:
        super().__init__(title=f"Báo lỗi: {ERROR_TYPES[key]['label']}"[:45])
        self.key = key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        now = int(time.time())

        last = db_one("SELECT MAX(created_at) AS t FROM reports WHERE user_id = ?", (uid,))
        if last and last["t"] and now - last["t"] < REPORT_COOLDOWN:
            wait = REPORT_COOLDOWN - (now - last["t"])
            await interaction.response.send_message(
                f"⏳ Bạn vừa gửi báo cáo xong, vui lòng đợi {wait} giây rồi thử lại.", ephemeral=True)
            return
        opened = db_one(
            "SELECT COUNT(*) AS c FROM reports WHERE user_id = ? AND status IN ('open','claimed')", (uid,))
        if opened and opened["c"] >= MAX_OPEN_REPORTS:
            await interaction.response.send_message(
                f"📌 Bạn đang có {opened['c']} báo cáo chưa xử lý xong. "
                "Vui lòng chờ đội ngũ phản hồi nhé!", ephemeral=True)
            return

        channel = await get_channel(STAFF_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message(
                "😵 Bot chưa được cấu hình kênh nhận báo cáo. Vui lòng liên hệ admin.", ephemeral=True)
            return

        report_id = db_exec(
            "INSERT INTO reports (user_id, error_key, target, device, isp, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, self.key, self.target.value, self.device.value, self.isp.value, self.detail.value, now),
        )
        row = db_one("SELECT * FROM reports WHERE id = ?", (report_id,))
        try:
            ping = f"<@&{SUPPORT_ROLE_ID}> " if SUPPORT_ROLE_ID else ""
            msg = await channel.send(
                content=f"{ping}🆕 Có báo cáo lỗi mới!",
                embed=report_embed(row),
                view=ReportActionView(),
                allowed_mentions=discord.AllowedMentions(roles=True, users=False),
            )
        except discord.HTTPException:
            db_exec("DELETE FROM reports WHERE id = ?", (report_id,))
            await interaction.response.send_message(
                "😵 Không gửi được báo cáo (bot thiếu quyền ở kênh staff). Vui lòng liên hệ admin.",
                ephemeral=True)
            return

        db_exec("UPDATE reports SET message_id = ?, channel_id = ? WHERE id = ?",
                (msg.id, msg.channel.id, report_id))
        await interaction.response.send_message(
            f"✅ Đã gửi báo cáo **#{report_id}** cho đội ngũ. Bạn sẽ được bot nhắn tin (DM) khi có kết quả - "
            "hãy bật nhận tin nhắn riêng từ thành viên server nhé!", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Lỗi khi gửi báo cáo", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("😵 Có lỗi xảy ra, vui lòng thử lại.", ephemeral=True)


# =====================================================================
#  UI: NÚT XỬ LÝ BÁO CÁO CHO STAFF (persistent)
# =====================================================================
class ReportActionView(discord.ui.View):
    def __init__(self, claimed: bool = False) -> None:
        super().__init__(timeout=None)
        self.claim.disabled = claimed

    async def _update(self, interaction: discord.Interaction, new_status: str) -> None:
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                "🚫 Chỉ đội ngũ hỗ trợ mới dùng được nút này.", ephemeral=True)
            return
        row = db_one("SELECT * FROM reports WHERE message_id = ?", (interaction.message.id,))
        if row is None:
            await interaction.response.send_message("Không tìm thấy báo cáo trong dữ liệu.", ephemeral=True)
            return

        db_exec("UPDATE reports SET status = ?, staff_id = ? WHERE id = ?",
                (new_status, interaction.user.id, row["id"]))
        row = db_one("SELECT * FROM reports WHERE id = ?", (row["id"],))

        closed = new_status in ("resolved", "norepro")
        await interaction.response.edit_message(
            content=interaction.message.content,
            embed=report_embed(row),
            view=None if closed else ReportActionView(claimed=True),
        )
        if closed:
            await notify_reporter(row)

    @discord.ui.button(label="Nhận xử lý", emoji="🙋", style=discord.ButtonStyle.primary,
                       custom_id="dutdiem:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._update(interaction, "claimed")

    @discord.ui.button(label="Đã xử lý", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id="dutdiem:resolve")
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._update(interaction, "resolved")

    @discord.ui.button(label="Không tái hiện được", emoji="🚫", style=discord.ButtonStyle.secondary,
                       custom_id="dutdiem:norepro")
    async def norepro(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._update(interaction, "norepro")


# =====================================================================
#  BOT
# =====================================================================
class SupportBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True                      # cần cho chào thành viên mới (bật trong Developer Portal)
        intents.message_content = ENABLE_AUTOREPLY  # chỉ cần khi bật tự gợi ý
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.web: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        init_db()
        self.web = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), headers=HTTP_HEADERS)
        self.add_view(PanelView())
        self.add_view(ReportActionView())

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        monitor_loop.start()

    async def close(self) -> None:
        monitor_loop.cancel()
        if self.web:
            await self.web.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Đã đăng nhập: %s (ID %s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=f"{SITE_NAME} | /baoloi"))


bot = SupportBot()


# =====================================================================
#  GIÁM SÁT WEBSITE TỰ ĐỘNG
# =====================================================================
monitor_state: dict[str, dict] = {}


@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_loop() -> None:
    try:
        channel = await get_channel(ALERT_CHANNEL_ID)
        for name, url in monitor_targets().items():
            result = await check_url(url)
            st = monitor_state.setdefault(name, {"fails": 0, "down_since": None, "alerted": False})

            if result.ok:
                if st["alerted"] and channel:
                    down_for = fmt_duration(time.time() - st["down_since"])
                    await channel.send(embed=discord.Embed(
                        title=f"🟢 {name} đã hoạt động trở lại",
                        description=f"Thời gian gián đoạn: **{down_for}**.\n"
                                    "Nếu bạn vẫn lỗi, hãy tải lại trang bằng **Ctrl + F5**.",
                        color=discord.Color.green(),
                        timestamp=datetime.now(timezone.utc),
                    ))
                st.update(fails=0, down_since=None, alerted=False)
                continue

            st["fails"] += 1
            if st["down_since"] is None:
                st["down_since"] = time.time()
            if st["fails"] >= FAIL_THRESHOLD and not st["alerted"]:
                st["alerted"] = True
                log.warning("%s không phản hồi: %s", name, result.error or result.status)
                if channel:
                    ping = f"<@&{SUPPORT_ROLE_ID}>" if SUPPORT_ROLE_ID else None
                    await channel.send(
                        content=ping,
                        embed=discord.Embed(
                            title=f"🔴 {name} đang gặp sự cố",
                            description="Hệ thống giám sát không nhận được phản hồi.\n"
                                        f"Lý do: `{result.error or 'HTTP ' + str(result.status)}`\n\n"
                                        "Đội ngũ đang kiểm tra. Bạn **không cần** gửi báo cáo thêm, "
                                        "bot sẽ thông báo khi hoạt động trở lại.",
                            color=discord.Color.red(),
                            timestamp=datetime.now(timezone.utc),
                        ),
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )
    except Exception:  # noqa: BLE001  (không để vòng lặp bị dừng vì lỗi lẻ)
        log.exception("Lỗi trong monitor_loop")


@monitor_loop.before_loop
async def before_monitor() -> None:
    await bot.wait_until_ready()


# =====================================================================
#  SỰ KIỆN
# =====================================================================
@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.bot:
        return
    support = f"<#{SUPPORT_CHANNEL_ID}>" if SUPPORT_CHANNEL_ID else "kênh hỗ trợ"
    embed = discord.Embed(
        title=f"👋 Chào mừng đến với cộng đồng {SITE_NAME}!",
        description=(
            f"Xin chào {member.mention}! Rất vui được đón bạn.\n\n"
            f"🔰 Dùng lệnh `/huongdan` để xem hướng dẫn cho người mới.\n"
            f"🆘 Gặp lỗi khi xem? Vào {support} hoặc dùng `/baoloi`.\n"
            f"🩺 Không chắc web có đang sập không? Dùng `/trangthai`."
        ),
        color=BRAND_COLOR,
    )
    channel = await get_channel(WELCOME_CHANNEL_ID)
    if channel:
        try:
            await channel.send(content=member.mention, embed=embed)
        except discord.HTTPException:
            log.warning("Không gửi được tin chào vào kênh welcome")
    if WELCOME_DM:
        try:
            await member.send(embed=embed)
        except discord.HTTPException:
            pass


autoreply_cooldown: dict[int, float] = {}


@bot.event
async def on_message(message: discord.Message) -> None:
    """Tự gợi ý cách khắc phục khi thành viên mô tả lỗi trong kênh hỗ trợ."""
    if message.author.bot or not ENABLE_AUTOREPLY or not SUPPORT_CHANNEL_ID:
        return
    if message.channel.id != SUPPORT_CHANNEL_ID:
        return
    now = time.time()
    if now - autoreply_cooldown.get(message.author.id, 0) < 60:
        return

    text = normalize(message.content)
    for key, pattern in AUTO_PATTERNS:
        if pattern.search(text):
            autoreply_cooldown[message.author.id] = now
            info = ERROR_TYPES[key]
            embed = discord.Embed(
                title=f"{info['emoji']} Có phải bạn đang gặp: {info['label']}?",
                description="Thử nhanh các bước sau:\n"
                            + "\n".join(f"**{i}.** {s}" for i, s in enumerate(info["steps"][:3], 1))
                            + "\n\nVẫn lỗi? Dùng `/baoloi` để gửi báo cáo cho đội ngũ.",
                color=discord.Color.orange(),
            )
            await message.reply(embed=embed, mention_author=False)
            return


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.error("Lỗi lệnh /%s", interaction.command.name if interaction.command else "?", exc_info=error)
    msg = "😵 Có lỗi xảy ra, vui lòng thử lại sau."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# =====================================================================
#  LỆNH SLASH
# =====================================================================
@bot.tree.command(name="baoloi", description="Báo lỗi / nhận hướng dẫn khắc phục khi xem không được")
@app_commands.describe(loai="Bạn đang gặp vấn đề gì?")
@app_commands.choices(loai=[app_commands.Choice(name=v["label"], value=k) for k, v in ERROR_TYPES.items()])
async def baoloi(interaction: discord.Interaction, loai: app_commands.Choice[str]) -> None:
    await send_troubleshoot(interaction, loai.value)


@bot.tree.command(name="trangthai", description="Kiểm tra website có đang hoạt động không")
async def trangthai(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    await interaction.followup.send(embed=await status_embed(), ephemeral=True)


@bot.tree.command(name="huongdan", description="Hướng dẫn cho người mới")
async def huongdan(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=guide_embed(), ephemeral=True)


@bot.tree.command(name="faq", description="Các câu hỏi thường gặp")
@app_commands.describe(cau_hoi="Chọn câu hỏi (bỏ trống để xem tất cả)")
@app_commands.choices(cau_hoi=[app_commands.Choice(name=q[:100], value=k) for k, (q, _) in FAQ.items()])
async def faq(interaction: discord.Interaction, cau_hoi: app_commands.Choice[str] | None = None) -> None:
    if cau_hoi is None:
        await interaction.response.send_message(embed=faq_list_embed(), ephemeral=True)
        return
    q, a = FAQ[cau_hoi.value]
    await interaction.response.send_message(
        embed=discord.Embed(title=f"❓ {q}", description=a, color=BRAND_COLOR), ephemeral=True)


@bot.tree.command(name="setup_panel", description="(Admin) Đăng bảng báo lỗi & hỗ trợ vào kênh này")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_panel(interaction: discord.Interaction) -> None:
    await interaction.channel.send(embed=panel_embed(), view=PanelView())
    await interaction.response.send_message("✅ Đã đăng bảng hỗ trợ vào kênh này.", ephemeral=True)


@bot.tree.command(name="dsbaocao", description="(Nhân viên) Xem các báo cáo lỗi đang mở")
@app_commands.guild_only()
async def dsbaocao(interaction: discord.Interaction) -> None:
    if not is_staff(interaction.user):
        await interaction.response.send_message("🚫 Lệnh này chỉ dành cho đội ngũ hỗ trợ.", ephemeral=True)
        return
    rows = db_all("SELECT * FROM reports WHERE status IN ('open','claimed') ORDER BY id DESC LIMIT 15")
    if not rows:
        await interaction.response.send_message("🎉 Không có báo cáo nào đang mở.", ephemeral=True)
        return
    lines = []
    for r in rows:
        info = ERROR_TYPES.get(r["error_key"], ERROR_TYPES["other"])
        status_text = STATUS_INFO[r["status"]][0]
        link = ""
        if r["message_id"] and r["channel_id"]:
            link = f" [xem](https://discord.com/channels/{interaction.guild_id}/{r['channel_id']}/{r['message_id']})"
        lines.append(f"`#{r['id']}` {info['emoji']} {info['label']} • <@{r['user_id']}> • "
                     f"<t:{r['created_at']}:R> • {status_text}{link}")
    await interaction.response.send_message(
        embed=discord.Embed(title="📋 Báo cáo đang mở", description="\n".join(lines), color=BRAND_COLOR),
        ephemeral=True)


# =====================================================================
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Thiếu DISCORD_TOKEN. Hãy tạo file .env (xem .env.example).")
    bot.run(TOKEN, log_handler=None)
