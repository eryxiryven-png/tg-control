import os, json, logging, threading, time
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import telebot
from telebot import types
from sqlalchemy import create_engine, text
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("controller")

TOKEN = os.environ["BOT_TOKEN"]  # Keep token in Render Environment, not in GitHub code.
ADMIN_ID = 6913254882
DATABASE_URL = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
BOT = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

# Conversation state is intentionally stored in PostgreSQL so restarts don't erase it.
def db(sql, params=None, fetch=False):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        if fetch:
            return [dict(r._mapping) for r in result]
        return result.rowcount

def init_db():
    statements = [
      """CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
        language TEXT DEFAULT 'en', status TEXT NOT NULL DEFAULT 'pending',
        joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
      """CREATE TABLE IF NOT EXISTS channels(
        chat_id BIGINT PRIMARY KEY, title TEXT, username TEXT,
        added_by BIGINT, added_at TIMESTAMPTZ DEFAULT NOW())""",
      """CREATE TABLE IF NOT EXISTS admins(user_id BIGINT PRIMARY KEY, added_at TIMESTAMPTZ DEFAULT NOW())""",
      """CREATE TABLE IF NOT EXISTS states(
        user_id BIGINT PRIMARY KEY, state TEXT, data JSONB NOT NULL DEFAULT '{}'::jsonb)""",
      """CREATE TABLE IF NOT EXISTS scheduled_posts(
        id BIGSERIAL PRIMARY KEY, owner_id BIGINT, chat_id BIGINT, body TEXT,
        buttons JSONB NOT NULL DEFAULT '[]'::jsonb, run_at TIMESTAMPTZ,
        reaction TEXT, status TEXT NOT NULL DEFAULT 'queued', created_at TIMESTAMPTZ DEFAULT NOW())"""
    ]
    with engine.begin() as c:
        for s in statements: c.execute(text(s))
        c.execute(text("INSERT INTO admins(user_id) VALUES (:id) ON CONFLICT DO NOTHING"), {"id": ADMIN_ID})

def is_admin(uid):
    return bool(db("SELECT 1 FROM admins WHERE user_id=:id", {"id":uid}, True))
def user_status(uid):
    rows=db("SELECT status FROM users WHERE user_id=:id", {"id":uid}, True)
    return rows[0]["status"] if rows else None
def set_state(uid, state, data=None):
    db("""INSERT INTO states(user_id,state,data) VALUES(:id,:s,CAST(:d AS jsonb))
       ON CONFLICT(user_id) DO UPDATE SET state=EXCLUDED.state,data=EXCLUDED.data""",
       {"id":uid,"s":state,"d":json.dumps(data or {})})
def get_state(uid):
    r=db("SELECT state,data FROM states WHERE user_id=:id", {"id":uid}, True)
    return (r[0]["state"], r[0]["data"]) if r else (None,{})
def clear_state(uid): db("DELETE FROM states WHERE user_id=:id", {"id":uid})
def lang(uid):
    r=db("SELECT language FROM users WHERE user_id=:id", {"id":uid}, True)
    return r[0]["language"] if r else "en"
def tr(uid, en, bn):
    return bn if lang(uid) == "bn" else en

def main_keyboard(uid):
    k=types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row("📣 Channels", "📝 Create post")
    k.row("⏰ Schedule post", "🌐 Language")
    k.row("🆘 Help")
    if is_admin(uid): k.row("🛠 Admin panel")
    return k
def admin_keyboard():
    k=types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row("📊 Statistics", "👥 User list")
    k.row("📡 Channel list", "➕ Add admin")
    k.row("✅ Pending users", "📨 Broadcast")
    k.row("⬅️ Main menu")
    return k
def inline_channels(uid, action):
    rows=db("SELECT chat_id,title,username FROM channels ORDER BY title", fetch=True)
    k=types.InlineKeyboardMarkup()
    for r in rows:
        label=(r["title"] or str(r["chat_id"]))[:50]
        k.add(types.InlineKeyboardButton(label, callback_data=f"{action}:{r['chat_id']}"))
    return k

def send_home(chat_id, uid):
    BOT.send_message(chat_id, tr(uid, "Choose an option using the keyboard.", "নিচের কিবোর্ড থেকে একটি অপশন বেছে নিন।"), reply_markup=main_keyboard(uid))

@BOT.message_handler(commands=["start"])
def start(m):
    u=m.from_user
    db("""INSERT INTO users(user_id,username,first_name,status) VALUES(:id,:un,:fn,:st)
       ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name""",
       {"id":u.id,"un":u.username,"fn":u.first_name,"st":"approved" if u.id==ADMIN_ID else "pending"})
    if u.id != ADMIN_ID and user_status(u.id) == "pending":
        BOT.send_message(m.chat.id, "👋 Welcome! Your access request has been sent to the admin.\nস্বাগতম! আপনার অনুমতির অনুরোধ অ্যাডমিনের কাছে পাঠানো হয়েছে।")
        for aid in [x["user_id"] for x in db("SELECT user_id FROM admins", fetch=True)]:
            try:
                k=types.InlineKeyboardMarkup()
                k.row(types.InlineKeyboardButton("✅ Approve", callback_data=f"approve:{u.id}"),
                      types.InlineKeyboardButton("❌ Reject", callback_data=f"reject:{u.id}"))
                BOT.send_message(aid, f"New access request\nName: {u.first_name}\nUsername: @{u.username or 'none'}\nID: <code>{u.id}</code>", reply_markup=k)
            except Exception: log.exception("admin notification failed")
        return
    send_home(m.chat.id,u.id)

@BOT.message_handler(commands=["help"])
def help_cmd(m):
    BOT.send_message(m.chat.id, "Use the keyboard to manage channels, create posts, add URL buttons, schedule posts, and contact the admin. The bot must be an administrator in each channel with posting permission.\n\nচ্যানেল ম্যানেজ, পোস্ট, URL বাটন, শিডিউল এবং অ্যাডমিনের সাহায্যের জন্য কিবোর্ড ব্যবহার করুন।", reply_markup=main_keyboard(m.from_user.id))

@BOT.message_handler(content_types=["new_chat_members"])
def added_to_group(m):
    # For channel registration, use /addchannel in the channel itself after promoting bot to admin.
    pass

@BOT.message_handler(commands=["addchannel"])
def add_channel(m):
    if m.chat.type == "private":
        BOT.reply_to(m, "Run /addchannel inside the target channel after making this bot an administrator.")
        return
    try:
        chat=BOT.get_chat(m.chat.id)
        db("""INSERT INTO channels(chat_id,title,username,added_by) VALUES(:id,:t,:u,:by)
        ON CONFLICT(chat_id) DO UPDATE SET title=EXCLUDED.title,username=EXCLUDED.username""",
        {"id":chat.id,"t":chat.title,"u":chat.username,"by":m.from_user.id})
        BOT.reply_to(m, "✅ Channel registered. You can now select it from the bot menu.")
    except Exception as e: BOT.reply_to(m, f"Could not register channel: {e}")

@BOT.message_handler(func=lambda m: True, content_types=["text"])
def text_router(m):
    uid=m.from_user.id; chat_id=m.chat.id; msg=(m.text or "").strip()
    if not is_admin(uid) and user_status(uid) != "approved":
        BOT.send_message(chat_id, "Your access is not approved yet. Tap 🆘 Help to contact admin.")
        return
    state,data=get_state(uid)
    if msg in ("🌐 Language","Language / ভাষা"):
        k=types.InlineKeyboardMarkup()
        k.row(types.InlineKeyboardButton("English",callback_data="lang:en"),types.InlineKeyboardButton("বাংলা",callback_data="lang:bn"))
        BOT.send_message(chat_id,"Choose language / ভাষা বেছে নিন",reply_markup=k); return
    if msg=="🆘 Help":
        set_state(uid,"help_message")
        BOT.send_message(chat_id,"Send your message to the admin now. / এখন অ্যাডমিনকে যে বার্তা দিতে চান লিখুন।")
        return
    if state=="help_message":
        clear_state(uid)
        for aid in [x["user_id"] for x in db("SELECT user_id FROM admins", fetch=True)]:
            try: BOT.send_message(aid,f"🆘 Help request from {uid} (@{m.from_user.username or 'none'}):\n{msg}")
            except Exception: pass
        BOT.send_message(chat_id,"Message sent to admin. / বার্তা পাঠানো হয়েছে।",reply_markup=main_keyboard(uid)); return
    if msg=="📣 Channels":
        BOT.send_message(chat_id,"Select a channel to manage. Add bot as channel admin, then send /addchannel in that channel.",reply_markup=inline_channels(uid,"channel"))
        return
    if msg=="📝 Create post":
        set_state(uid,"post_target",{})
        BOT.send_message(chat_id,"Select a channel:",reply_markup=inline_channels(uid,"target"))
        return
    if msg=="⏰ Schedule post":
        set_state(uid,"schedule_target",{})
        BOT.send_message(chat_id,"Select a channel:",reply_markup=inline_channels(uid,"schedtarget"))
        return
    if msg=="🛠 Admin panel" and is_admin(uid):
        BOT.send_message(chat_id,"Admin control panel",reply_markup=admin_keyboard()); return
    if msg=="⬅️ Main menu":
        send_home(chat_id,uid); return
    if is_admin(uid) and msg=="📊 Statistics":
        u=db("SELECT COUNT(*) n FROM users",fetch=True)[0]["n"]
        approved=db("SELECT COUNT(*) n FROM users WHERE status='approved'",fetch=True)[0]["n"]
        pending=db("SELECT COUNT(*) n FROM users WHERE status='pending'",fetch=True)[0]["n"]
        ch=db("SELECT COUNT(*) n FROM channels",fetch=True)[0]["n"]
        BOT.send_message(chat_id,f"📊 Statistics\nUsers: {u}\nApproved: {approved}\nPending: {pending}\nChannels: {ch}",reply_markup=admin_keyboard()); return
    if is_admin(uid) and msg=="👥 User list":
        rows=db("SELECT user_id,username,status FROM users ORDER BY joined_at DESC LIMIT 100",fetch=True)
        BOT.send_message(chat_id,"\n".join(f"{r['user_id']} @{r['username'] or '-'} — {r['status']}" for r in rows) or "No users"); return
    if is_admin(uid) and msg=="📡 Channel list":
        rows=db("SELECT chat_id,title,username FROM channels ORDER BY title",fetch=True)
        BOT.send_message(chat_id,"\n".join(f"{r['title']} | {r['chat_id']} | @{r['username'] or '-'}" for r in rows) or "No channels"); return
    if is_admin(uid) and msg=="➕ Add admin":
        set_state(uid,"add_admin")
        BOT.send_message(chat_id,"Send the numeric Telegram user ID to promote as admin.")
        return
    if is_admin(uid) and state=="add_admin":
        try:
            new_id=int(msg); db("INSERT INTO admins(user_id) VALUES(:id) ON CONFLICT DO NOTHING",{"id":new_id})
            clear_state(uid); BOT.send_message(chat_id,f"✅ Admin added: {new_id}",reply_markup=admin_keyboard())
        except ValueError: BOT.send_message(chat_id,"Please send a numeric user ID.")
        return
    if is_admin(uid) and msg=="✅ Pending users":
        rows=db("SELECT user_id,username,first_name FROM users WHERE status='pending' ORDER BY joined_at",fetch=True)
        for r in rows:
            k=types.InlineKeyboardMarkup().row(types.InlineKeyboardButton("✅ Approve",callback_data=f"approve:{r['user_id']}"),types.InlineKeyboardButton("❌ Reject",callback_data=f"reject:{r['user_id']}"))
            BOT.send_message(chat_id,f"{r['first_name']} @{r['username'] or '-'} — <code>{r['user_id']}</code>",reply_markup=k)
        if not rows: BOT.send_message(chat_id,"No pending users.")
        return
    if is_admin(uid) and msg=="📨 Broadcast":
        set_state(uid,"broadcast")
        BOT.send_message(chat_id,"Send the text to broadcast to all approved bot users and registered channels.")
        return
    if is_admin(uid) and state=="broadcast":
        clear_state(uid); ok=0; fail=0
        for r in db("SELECT user_id FROM users WHERE status='approved'",fetch=True):
            try: BOT.send_message(r["user_id"],msg); ok+=1
            except Exception: fail+=1
        for r in db("SELECT chat_id FROM channels",fetch=True):
            try: BOT.send_message(r["chat_id"],msg); ok+=1
            except Exception: fail+=1
        BOT.send_message(chat_id,f"Broadcast finished.\nSent: {ok}\nFailed: {fail}",reply_markup=admin_keyboard()); return
    if state in ("post_body","schedule_body"):
        data["body"]=msg
        set_state(uid,state+"_buttons",data)
        BOT.send_message(chat_id,"Now send button links as: Button title | https://example.com\nSend NONE for no button. Multiple links: one per line.")
        return
    if state in ("post_body_buttons","schedule_body_buttons"):
        if msg.upper()!="NONE":
            try:
                lines=msg.splitlines(); buttons=[]
                for line in lines:
                    title,url=[x.strip() for x in line.split("|",1)]
                    if not url.startswith(("https://","http://")): raise ValueError()
                    buttons.append({"text":title,"url":url})
                data["buttons"]=buttons
            except Exception:
                BOT.send_message(chat_id,"Invalid format. Use: Button title | https://example.com (one per line), or NONE.")
                return
        else: data["buttons"]=[]
        if state.startswith("schedule"):
            set_state(uid,"schedule_time",data)
            BOT.send_message(chat_id,"Send scheduled time in UTC, format YYYY-MM-DD HH:MM (example 2026-10-20 14:30).")
        else:
            set_state(uid,"post_reaction",data)
            BOT.send_message(chat_id,"Send a reaction emoji (example 👍) or NONE.")
        return
    if state=="post_reaction":
        data["reaction"]=None if msg.upper()=="NONE" else msg
        try: publish(data["chat_id"],data["body"],data.get("buttons",[]),data.get("reaction"))
        except Exception as e: BOT.send_message(chat_id,f"Post failed: {e}"); clear_state(uid); return
        clear_state(uid); BOT.send_message(chat_id,"✅ Posted.",reply_markup=main_keyboard(uid)); return
    if state=="schedule_time":
        try:
            dt=datetime.strptime(msg,"%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if dt <= datetime.now(timezone.utc): raise ValueError()
            data["run_at"]=dt.isoformat()
            set_state(uid,"schedule_reaction",data)
            BOT.send_message(chat_id,"Send reaction emoji or NONE.")
        except ValueError: BOT.send_message(chat_id,"Invalid/past time. Use YYYY-MM-DD HH:MM UTC.")
        return
    if state=="schedule_reaction":
        reaction=None if msg.upper()=="NONE" else msg
        db("""INSERT INTO scheduled_posts(owner_id,chat_id,body,buttons,run_at,reaction)
          VALUES(:o,:c,:b,CAST(:btn AS jsonb),:rt,:re)""",
          {"o":uid,"c":data["chat_id"],"b":data["body"],"btn":json.dumps(data.get("buttons",[])),"rt":data["run_at"],"re":reaction})
        clear_state(uid); BOT.send_message(chat_id,"⏰ Scheduled successfully (UTC).",reply_markup=main_keyboard(uid)); return
    BOT.send_message(chat_id,"Use the keyboard to continue.",reply_markup=main_keyboard(uid))

def publish(chat_id, body, buttons=None, reaction=None):
    markup=None
    if buttons:
        markup=types.InlineKeyboardMarkup()
        for b in buttons: markup.add(types.InlineKeyboardButton(b["text"],url=b["url"]))
    sent=BOT.send_message(chat_id,body,reply_markup=markup)
    if reaction:
        try: BOT.set_message_reaction(chat_id,sent.message_id,[types.ReactionTypeEmoji(emoji=reaction)])
        except Exception: log.warning("Reaction could not be applied; check Bot API/library/channel permissions")

@BOT.callback_query_handler(func=lambda c: True)
def callbacks(c):
    uid=c.from_user.id; data=c.data or ""
    try:
        if data.startswith("lang:"):
            choice=data.split(":",1)[1]
            db("UPDATE users SET language=:l WHERE user_id=:id",{"l":choice,"id":uid})
            BOT.answer_callback_query(c.id,"Language updated")
            BOT.send_message(c.message.chat.id,"Language updated.",reply_markup=main_keyboard(uid)); return
        if data.startswith(("approve:","reject:")) and is_admin(uid):
            action,target=data.split(":"); target=int(target)
            status="approved" if action=="approve" else "rejected"
            db("UPDATE users SET status=:s WHERE user_id=:id",{"s":status,"id":target})
            BOT.answer_callback_query(c.id,status)
            try: BOT.send_message(target,"✅ Access approved. Send /start to begin." if status=="approved" else "Your access request was rejected.")
            except Exception: pass
            BOT.edit_message_reply_markup(c.message.chat.id,c.message.message_id,reply_markup=None); return
        if ":" in data and data.split(":",1)[0] in ("channel","target","schedtarget"):
            action,chat=data.split(":",1); chat=int(chat)
            state,_=get_state(uid)
            if action=="channel":
                k=types.InlineKeyboardMarkup().row(types.InlineKeyboardButton("📝 Create post",callback_data=f"target:{chat}"),types.InlineKeyboardButton("⏰ Schedule",callback_data=f"schedtarget:{chat}"))
                BOT.send_message(c.message.chat.id,"Channel selected:",reply_markup=k)
            elif action=="target":
                set_state(uid,"post_body",{"chat_id":chat})
                BOT.send_message(c.message.chat.id,"Send the post text now.")
            else:
                set_state(uid,"schedule_body",{"chat_id":chat})
                BOT.send_message(c.message.chat.id,"Send the post text now.")
            BOT.answer_callback_query(c.id); return
    except Exception as e:
        log.exception("callback error")
        try: BOT.answer_callback_query(c.id,str(e)[:180],show_alert=True)
        except Exception: pass

def run_scheduled():
    try:
        due=db("SELECT * FROM scheduled_posts WHERE status='queued' AND run_at<=NOW() ORDER BY run_at LIMIT 20",fetch=True)
        for p in due:
            changed=db("UPDATE scheduled_posts SET status='sending' WHERE id=:id AND status='queued'",{"id":p["id"]})
            if not changed: continue
            try:
                buttons=p["buttons"] if isinstance(p["buttons"],list) else json.loads(p["buttons"] or "[]")
                publish(p["chat_id"],p["body"],buttons,p["reaction"])
                db("UPDATE scheduled_posts SET status='sent' WHERE id=:id",{"id":p["id"]})
            except Exception:
                log.exception("scheduled post failed")
                db("UPDATE scheduled_posts SET status='failed' WHERE id=:id",{"id":p["id"]})
    except Exception: log.exception("scheduler tick failed")

@app.get("/")
def home(): return "Telegram Channel Controller is running", 200
@app.get("/healthz")
def health(): return jsonify(status="ok"), 200
if __name__ == "__main__":
    init_db()
    scheduler=BackgroundScheduler(daemon=True)
    scheduler.add_job(run_scheduled,"interval",seconds=20,id="scheduled-posts",max_instances=1,coalesce=True)
    scheduler.start()

    # Long polling and webhook cannot be active at the same time.
    try:
        BOT.remove_webhook(drop_pending_updates=False)
    except Exception:
        log.exception("Could not remove old webhook")
    threading.Thread(
        target=lambda: BOT.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=25),
        daemon=True
    ).start()

    # Render Web Service health endpoint; bot updates are received via polling, not webhook.
    port=int(os.environ.get("PORT","10000"))
    app.run(host="0.0.0.0",port=port)
