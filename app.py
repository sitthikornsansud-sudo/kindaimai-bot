# -*- coding: utf-8 -*-
"""
กินได้มั้ย (KinDaiMai) — LINE Bot สแกนฉลากเพื่อผู้ป่วย G6PD และผู้แพ้อาหาร
Samsung Solve for Tomorrow 2026

การทำงาน:
  ผู้ใช้ส่งรูปฉลาก → Google Vision OCR อ่านข้อความ → เทียบฐานข้อมูลสารต้องห้าม
  ตามโปรไฟล์โรคของผู้ใช้ → ตอบกลับการ์ดสี เขียว/เหลือง/แดง + วันหมดอายุ

คำสั่งในแชท:
  "โปรไฟล์"        ดู/เลือกโรคของตัวเอง (กดปุ่มเลือกได้เลย)
  ส่งรูปฉลาก        วิเคราะห์ทันที
  "ยา <ชื่อยา>"     เช็คว่ายาอยู่ในกลุ่มเสี่ยงของ G6PD ไหม
  "ช่วยเหลือ"       วิธีใช้งาน

⚠️ ฐานข้อมูลเวอร์ชันต้นแบบ — ต้องผ่านการตรวจสอบโดยแพทย์/เภสัชกรก่อนใช้จริง
"""

import os
import re
import base64
import datetime
import requests
from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer,
    QuickReply, QuickReplyItem, MessageAction,
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, ImageMessageContent, FollowEvent,
)

# ══════════════ ค่าตั้งต้น (ใส่ผ่าน Environment Variables) ══════════════
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
GOOGLE_VISION_API_KEY = os.environ.get("GOOGLE_VISION_API_KEY", "")

app = Flask(__name__)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# โปรไฟล์ผู้ใช้เก็บในหน่วยความจำ (หายเมื่อรีสตาร์ท — เวอร์ชันจริงค่อยย้ายไป Google Sheets/ฐานข้อมูล)
USER_PROFILES = {}  # {user_id: set(["g6pd", "peanut", ...])}

# ══════════════ ฐานข้อมูล (ตรงกับเว็บแอปต้นแบบ) ══════════════
DISEASES = {
    "g6pd": "G6PD",
    "peanut": "แพ้ถั่วลิสง",
    "milk": "แพ้นมวัว",
    "egg": "แพ้ไข่",
    "seafood": "แพ้อาหารทะเล",
    "wheat": "แพ้แป้งสาลี",
    "soy": "แพ้ถั่วเหลือง",
}

FOOD_DB = {
    "g6pd": [
        {"name": "สารกันเสียกลุ่มซัลไฟต์", "level": "danger",
         "note": "อาจทำให้เม็ดเลือดแดงแตกในผู้ป่วย G6PD",
         "aliases": ["ซัลไฟต์", "ซัลเฟอร์ไดออกไซด์", "โซเดียมซัลไฟต์", "โซเดียมไบซัลไฟต์",
                      "โซเดียมเมตาไบซัลไฟต์", "โพแทสเซียมเมตาไบซัลไฟต์", "sulfite", "sulphite",
                      "ins 220", "ins 221", "ins 222", "ins 223", "ins 224", "ins 225", "ins 228",
                      "e220", "e221", "e222", "e223", "e224", "e225", "e228"]},
        {"name": "ถั่วปากอ้า", "level": "danger",
         "note": "ของต้องห้ามอันดับหนึ่งของผู้ป่วย G6PD",
         "aliases": ["ถั่วปากอ้า", "fava", "broad bean", "ถั่วฟาวา"]},
        {"name": "การบูร", "level": "danger",
         "note": "กระตุ้นให้เม็ดเลือดแดงแตกได้",
         "aliases": ["การบูร", "camphor"]},
        {"name": "เมนทอล", "level": "caution",
         "note": "ปริมาณมากอาจเป็นความเสี่ยง ควรปรึกษาแพทย์",
         "aliases": ["เมนทอล", "menthol"]},
        {"name": "ควินิน", "level": "danger",
         "note": "พบในน้ำโทนิค เป็นสารเสี่ยงของ G6PD",
         "aliases": ["ควินิน", "quinine", "โทนิค", "tonic"]},
    ],
    "peanut": [
        {"name": "ถั่วลิสง", "level": "danger", "note": "สารก่อภูมิแพ้ตามโปรไฟล์ของคุณ",
         "aliases": ["ถั่วลิสง", "peanut", "เนยถั่ว", "กากถั่วลิสง", "น้ำมันถั่วลิสง"]},
    ],
    "milk": [
        {"name": "นมวัวและผลิตภัณฑ์นม", "level": "danger", "note": "สารก่อภูมิแพ้ตามโปรไฟล์ของคุณ",
         "aliases": ["นมวัว", "นมผง", "นมสด", "หางนม", "เวย์", "whey", "เคซีน", "casein",
                      "แลคโตส", "lactose", "ครีมเทียม", "เนยสด", "butter", "ชีส", "cheese",
                      "โยเกิร์ต", "นมข้น"]},
    ],
    "egg": [
        {"name": "ไข่", "level": "danger", "note": "สารก่อภูมิแพ้ตามโปรไฟล์ของคุณ",
         "aliases": ["ไข่ไก่", "ไข่ขาว", "ไข่แดง", "ไข่ผง", "อัลบูมิน", "albumin", "ไข่เป็ด"]},
    ],
    "seafood": [
        {"name": "อาหารทะเล", "level": "danger", "note": "สารก่อภูมิแพ้ตามโปรไฟล์ของคุณ",
         "aliases": ["กุ้ง", "ปู", "หอย", "ปลาหมึก", "ปลา", "น้ำปลา", "กะปิ", "ซอสหอยนางรม",
                      "shrimp", "crab", "fish", "squid"]},
    ],
    "wheat": [
        {"name": "แป้งสาลี / กลูเตน", "level": "danger", "note": "สารก่อภูมิแพ้ตามโปรไฟล์ของคุณ",
         "aliases": ["แป้งสาลี", "wheat", "กลูเตน", "gluten", "มอลต์", "malt", "บาร์เลย์", "barley"]},
    ],
    "soy": [
        {"name": "ถั่วเหลือง", "level": "danger", "note": "สารก่อภูมิแพ้ตามโปรไฟล์ของคุณ",
         "aliases": ["ถั่วเหลือง", "soy", "ซีอิ๊ว", "เต้าเจี้ยว", "โปรตีนถั่วเหลือง",
                      "เลซิติน", "lecithin", "เต้าหู้"]},
    ],
}

DRUG_DB = [
    {"name": "ยากลุ่มซัลฟา (Sulfonamides)",
     "aliases": ["ซัลฟา", "sulfa", "sulfamethoxazole", "ซัลฟาเมทอกซาโซล",
                  "co-trimoxazole", "โคไตรม็อกซาโซล", "bactrim", "แบคทริม", "sulfadiazine"]},
    {"name": "แอสไพริน", "aliases": ["แอสไพริน", "aspirin", "asa", "acetylsalicylic"]},
    {"name": "คลอแรมเฟนิคอล", "aliases": ["คลอแรมเฟนิคอล", "chloramphenicol"]},
    {"name": "ไนโตรฟูแรนโทอิน", "aliases": ["ไนโตรฟูแรนโทอิน", "nitrofurantoin"]},
    {"name": "ไพรมาควีน (ยาต้านมาลาเรีย)", "aliases": ["ไพรมาควีน", "primaquine"]},
    {"name": "คลอโรควิน (ยาต้านมาลาเรีย)", "aliases": ["คลอโรควิน", "chloroquine"]},
    {"name": "แดปโซน", "aliases": ["แดปโซน", "dapsone"]},
    {"name": "เมทิลีนบลู", "aliases": ["เมทิลีนบลู", "methylene blue"]},
    {"name": "ควินิน", "aliases": ["ควินิน", "quinine"]},
    {"name": "กรดนาลิดิซิก", "aliases": ["นาลิดิซิก", "nalidixic"]},
    {"name": "ราสบูริเคส", "aliases": ["ราสบูริเคส", "rasburicase"]},
    {"name": "ฟีนาโซไพริดีน", "aliases": ["ฟีนาโซไพริดีน", "phenazopyridine"]},
]

DISCLAIMER = "ผลนี้เป็นการช่วยตัดสินใจเบื้องต้นจากฐานข้อมูลต้นแบบ หากไม่แน่ใจ อย่าบริโภค และปรึกษาแพทย์หรือเภสัชกร"

# สีธีม ชมพู × มิ้นต์ × ช็อกโกแลต (ตรงกับเว็บแอป)
C_PINK = "#F27BA6"
C_MINT = "#178A70"
C_RED = "#D93A5F"
C_AMBER = "#C98A1C"
C_CREAM = "#FFFDF8"
C_CHOCO = "#6B4A38"


# ══════════════ OCR ด้วย Google Vision (เรียกผ่าน REST + API Key) ══════════════
def ocr_image(image_bytes: bytes) -> str:
    url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"
    payload = {
        "requests": [{
            "image": {"content": base64.b64encode(image_bytes).decode()},
            "features": [{"type": "TEXT_DETECTION"}],
            "imageContext": {"languageHints": ["th", "en"]},
        }]
    }
    r = requests.post(url, json=payload, timeout=25)
    r.raise_for_status()
    data = r.json()
    try:
        return data["responses"][0]["fullTextAnnotation"]["text"]
    except (KeyError, IndexError):
        return ""


# ══════════════ หาวันหมดอายุจากข้อความ OCR ══════════════
def find_expiry(text: str):
    """คืน (ข้อความดิบ, วันที่ python หรือ None) รองรับ พ.ศ./ค.ศ. และปี 2 หลัก"""
    t = text.upper()
    pat = re.compile(
        r"(EXP\.?|EXD|BBF|BBE|หมดอายุ|ควรบริโภคก่อน)[^0-9]{0,12}"
        r"(\d{1,2})[\s/\-.](\d{1,2})[\s/\-.](\d{2,4})")
    m = pat.search(t)
    if not m:
        # แบบ เดือน/ปี เช่น EXP 12/26
        pat2 = re.compile(r"(EXP\.?|BBF|หมดอายุ)[^0-9]{0,12}(\d{1,2})[\s/\-.](\d{2,4})")
        m2 = pat2.search(t)
        if not m2:
            return None, None
        mo, yr = int(m2.group(2)), int(m2.group(3))
        yr = normalize_year(yr)
        try:
            # ให้เป็นวันสุดท้ายของเดือน
            nxt = datetime.date(yr + (mo == 12), (mo % 12) + 1, 1)
            return m2.group(0), nxt - datetime.timedelta(days=1)
        except ValueError:
            return m2.group(0), None
    d, mo, yr = int(m.group(2)), int(m.group(3)), int(m.group(4))
    yr = normalize_year(yr)
    try:
        return m.group(0), datetime.date(yr, mo, d)
    except ValueError:
        return m.group(0), None


def normalize_year(yr: int) -> int:
    if yr < 100:                # ปี 2 หลัก: 26 → 2026, 69 → 2569 → 2026
        yr = 2500 + yr if yr >= 60 else 2000 + yr
    if yr > 2400:               # พ.ศ. → ค.ศ.
        yr -= 543
    return yr


# ══════════════ วิเคราะห์ข้อความฉลากตามโปรไฟล์ ══════════════
def analyze_label(text: str, profile_ids):
    hay = text.lower()
    hits = []
    for pid in profile_ids:
        for item in FOOD_DB.get(pid, []):
            for a in item["aliases"]:
                if a.lower() in hay:
                    hits.append({**item, "matched": a, "disease": DISEASES[pid]})
                    break
    exp_raw, exp_date = find_expiry(text)
    expired = bool(exp_date and exp_date < datetime.date.today())
    danger = any(h["level"] == "danger" for h in hits) or expired
    caution = any(h["level"] == "caution" for h in hits)
    verdict = "danger" if danger else ("caution" if caution else "safe")
    return {"verdict": verdict, "hits": hits, "exp_raw": exp_raw, "expired": expired}


# ══════════════ สร้างการ์ดผลลัพธ์ (Flex Message) ══════════════
def result_flex(res):
    style = {
        "danger": (C_RED, "🙅 ห้ามกิน", "พบสารเสี่ยงตามโปรไฟล์ของคุณ" if res["hits"] else "สินค้าหมดอายุแล้ว"),
        "caution": (C_AMBER, "🤔 ควรระวัง", "พบสารที่ควรปรึกษาแพทย์ก่อนบริโภค"),
        "safe": (C_MINT, "😋 กินได้", "ไม่พบสารต้องห้ามตามโปรไฟล์ในฐานข้อมูล"),
    }[res["verdict"]]
    color, stamp, sub = style

    body = [
        {"type": "text", "text": sub, "weight": "bold", "wrap": True, "size": "md", "color": C_CHOCO},
        {"type": "text", "size": "sm", "wrap": True, "color": C_CHOCO, "margin": "md",
         "text": "📅 วันหมดอายุ: " + (res["exp_raw"] or "อ่านไม่พบบนฉลาก")
                 + (" — หมดอายุแล้ว!" if res["expired"] else "")},
    ]
    if res["hits"]:
        body.append({"type": "separator", "margin": "md"})
        for h in res["hits"][:5]:
            body.append({"type": "text", "wrap": True, "margin": "md", "size": "sm",
                         "color": C_RED if h["level"] == "danger" else C_AMBER,
                         "text": f"⚠️ {h['name']} (พบคำว่า “{h['matched']}” · {h['disease']})"})
            body.append({"type": "text", "wrap": True, "size": "xs", "color": C_CHOCO,
                         "text": h["note"]})
    body.append({"type": "text", "text": DISCLAIMER, "wrap": True, "size": "xxs",
                 "color": "#9C7A63", "margin": "lg"})

    bubble = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": color,
                   "contents": [{"type": "text", "text": stamp, "color": "#FFFFFF",
                                  "weight": "bold", "size": "xxl", "align": "center"}]},
        "body": {"type": "box", "layout": "vertical", "backgroundColor": C_CREAM,
                 "contents": body},
    }
    return FlexMessage(alt_text=stamp, contents=FlexContainer.from_dict(bubble))


# ══════════════ Quick Reply เลือกโปรไฟล์ ══════════════
def profile_quick_reply(user_id):
    current = USER_PROFILES.get(user_id, {"g6pd"})
    items = []
    for pid, label in DISEASES.items():
        mark = "✓" if pid in current else "＋"
        items.append(QuickReplyItem(
            action=MessageAction(label=f"{mark} {label}"[:20], text=f"เลือก {label}")))
    return QuickReply(items=items)


def profile_text(user_id):
    current = USER_PROFILES.get(user_id, {"g6pd"})
    names = ", ".join(DISEASES[p] for p in current) or "ยังไม่ได้เลือก"
    return (f"🌸 โปรไฟล์ตอนนี้: {names}\n\n"
            "แตะปุ่มด้านล่างเพื่อเพิ่ม/เอาออกได้เลย แล้วส่งรูปฉลากมาได้ทันที 📸")


HELP_TEXT = (
    "🍭 วิธีใช้ กินได้มั้ย\n\n"
    "1) พิมพ์ \"โปรไฟล์\" เพื่อเลือกโรคของคุณ\n"
    "2) ส่ง \"รูปถ่ายฉลากส่วนประกอบ\" มาได้เลย เดี๋ยวเช็คให้ทันที\n"
    "3) พิมพ์ \"ยา ตามด้วยชื่อยา\" เช่น \"ยา aspirin\" เพื่อเช็คยากลุ่มเสี่ยง G6PD\n\n"
    "⚠️ แอปนี้เป็นต้นแบบเพื่อการประกวด ไม่ใช้แทนคำแนะนำของแพทย์/เภสัชกร"
)


# ══════════════ เส้นทางรับ Webhook จาก LINE ══════════════
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@app.route("/", methods=["GET"])
def health():
    return "กินได้มั้ย bot is running 🍭"


def reply(event, messages):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=messages))


# ══════════════ เหตุการณ์: เพิ่มเพื่อน ══════════════
@handler.add(FollowEvent)
def on_follow(event):
    uid = event.source.user_id
    USER_PROFILES.setdefault(uid, {"g6pd"})
    reply(event, [
        TextMessage(text="สวัสดี! เราคือ \"กินได้มั้ย\" 🍭\nสแกนก่อนกิน มั่นใจทุกคำ 💕\n\n" + HELP_TEXT),
        TextMessage(text=profile_text(uid), quick_reply=profile_quick_reply(uid)),
    ])


# ══════════════ เหตุการณ์: ข้อความตัวอักษร ══════════════
@handler.add(MessageEvent, message=TextMessageContent)
def on_text(event):
    uid = event.source.user_id
    text = event.message.text.strip()
    USER_PROFILES.setdefault(uid, {"g6pd"})

    if text in ("โปรไฟล์", "profile"):
        reply(event, [TextMessage(text=profile_text(uid),
                                  quick_reply=profile_quick_reply(uid))])
        return

    if text.startswith("เลือก "):
        label = text[6:].strip()
        pid = next((k for k, v in DISEASES.items() if v == label), None)
        if pid:
            cur = USER_PROFILES[uid]
            if pid in cur and len(cur) > 1:
                cur.remove(pid)
            else:
                cur.add(pid)
        reply(event, [TextMessage(text=profile_text(uid),
                                  quick_reply=profile_quick_reply(uid))])
        return

    if text.startswith("ยา ") or text.startswith("ya "):
        q = text.split(" ", 1)[1].strip().lower()
        hit = next((d for d in DRUG_DB
                    if any(a.lower() in q or q in a.lower() for a in d["aliases"])), None)
        if hit:
            msg = (f"🙅 พบในกลุ่มยาเสี่ยงของ G6PD\n\n{hit['name']}\n\n"
                   "ห้ามใช้ยานี้เองเด็ดขาด แจ้งแพทย์หรือเภสัชกรว่าคุณเป็น G6PD ทุกครั้งก่อนรับยา")
        else:
            msg = ("🌈 ไม่พบในฐานข้อมูลยาเสี่ยง (ต้นแบบ)\n\n"
                   "ฐานข้อมูลนี้ยังไม่ครบทุกรายการ — ควรแจ้งเภสัชกรว่าเป็น G6PD ทุกครั้งที่รับยา")
        reply(event, [TextMessage(text=msg)])
        return

    reply(event, [TextMessage(text=HELP_TEXT)])


# ══════════════ เหตุการณ์: รูปภาพ (หัวใจของบอท) ══════════════
@handler.add(MessageEvent, message=ImageMessageContent)
def on_image(event):
    uid = event.source.user_id
    profiles = USER_PROFILES.setdefault(uid, {"g6pd"})
    try:
        with ApiClient(configuration) as api_client:
            image_bytes = MessagingApiBlob(api_client).get_message_content(event.message.id)
        text = ocr_image(image_bytes)
        if not text.strip():
            reply(event, [TextMessage(
                text="อ่านตัวหนังสือจากรูปไม่ได้เลย 😢\nลองถ่ายใหม่ให้เห็นคำว่า “ส่วนประกอบ” ชัดๆ แสงสว่าง ไม่สะท้อนนะ")])
            return
        res = analyze_label(text, profiles)
        reply(event, [result_flex(res)])
    except Exception as e:
        print("ERROR:", e)
        reply(event, [TextMessage(text="ระบบขัดข้องชั่วคราว ลองส่งรูปอีกครั้งนะ 🙏")])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
