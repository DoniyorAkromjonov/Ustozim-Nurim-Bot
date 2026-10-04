"""Ustozim Nurim Telegram bot. Aiogram polling + SQLite, external services not required."""
import asyncio
import json
import logging
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode, PollType
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (Message, PollAnswer, Poll, CallbackQuery, KeyboardButton,
    ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
AI_TOKEN = (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")).strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN topilmadi. Local .env yoki hosting Environment Variables sozlamasini tekshiring.")

# Oldingi wrangler.toml'dan ko'chirilgan. Istasangiz .env orqali o'zgartiring.
GROUP_ID = os.getenv("CHAT_ID", "-1001920805324")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1272696340"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@sahifalarUstozimnurim")
GROUP_URL = os.getenv("GROUP_URL", "https://t.me/ustozimnurimco")
BOT_USERNAME = os.getenv("BOT_USERNAME", "s1mple_reaction_3_bot")
KITOB_START = date.fromisoformat(os.getenv("KITOB_START", "2026-08-08"))
TZ = ZoneInfo("Asia/Tashkent")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "ustozim_quiz.db")))
QUIZ_BANK = json.loads((ROOT / "quiz_bank.json").read_text(encoding="utf-8"))
BOOK_BANK = json.loads((ROOT / "kitob_bank.json").read_text(encoding="utf-8"))
TOPICS = {"IT": "💻 IT", "Liderlik": "🌱 Liderlik", "Diniy": "🕌 Islomiy", "Kino": "🎬 Kino", "Mantiq": "🧩 Mantiqiy", "Matem": "➗ Matem", "Ingliz": "🔤 Ingliz", "Kitob": "📖 Kitob"}
CATEGORY_LABELS = {
    "aralash": "🔀 Aralash", "it": "💻 IT", "matem": "➗ Matem", "ingliz": "🔤 Ingliz",
    "mantiqiy": "🧩 Mantiqiy", "islomiy": "🕌 Islomiy", "kitobdan": "📖 Kitobdan", "viktorina": "🎯 Viktorina",
}
FASL_NAMES = [
    "Ilm va fiqhning mohiyati va fazilati", "Ilm olishda niyat qilish", "Ilm, ustoz va sherik tanlash",
    "Ilm va ilm ahlini ulug'lash", "Jiddu-jahd, bardavomlik va himmat", "Darsni boshlash, miqdori va tartibi",
    "Tavakkul qilish", "Tahsil vaqti", "Nasihat va mehru shafqat", "Foyda talab qilish",
    "Ilm olishda parhez va taqvo", "Zehnni mustahkamlaydigan omillar", "Rizq va umrni ziyoda qiladiganlar",
    "Imom A'zamning Abu Yusufga vasiyati"]
MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🧠 Kunlik viktorina"), KeyboardButton(text="📖 Kitob marafoni")],
    [KeyboardButton(text="📚 Kategoriyalar"), KeyboardButton(text="📚 O'z tezligimda")],
    [KeyboardButton(text="📊 Natijalarim"), KeyboardButton(text="🏆 Reyting")],
    [KeyboardButton(text="ℹ️ Yordam")]], resize_keyboard=True)
dp = Dispatcher()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY, name TEXT, username TEXT, seq INTEGER DEFAULT 0,
          answered INTEGER DEFAULT 0, correct INTEGER DEFAULT 0, streak INTEGER DEFAULT 0, last_daily TEXT,
          days INTEGER DEFAULT 0, kitob INTEGER DEFAULT 0, kitob_ball INTEGER DEFAULT 0, kitob_javob INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS answers(uid INTEGER, day TEXT, mode TEXT, stage INTEGER, idx INTEGER, correct INTEGER, topic TEXT DEFAULT '',
          PRIMARY KEY(uid,day,mode,stage,idx));
        CREATE TABLE IF NOT EXISTS polls(poll_id TEXT PRIMARY KEY, uid INTEGER, chat_id INTEGER, mode TEXT,
          stage INTEGER, idx INTEGER, question TEXT, options TEXT, correct_idx INTEGER, total INTEGER);
        CREATE TABLE IF NOT EXISTS daily_quiz(day TEXT PRIMARY KEY, questions TEXT);
        """)
        cols=[r[1] for r in await (await db.execute("PRAGMA table_info(answers)")).fetchall()]
        if "topic" not in cols: await db.execute("ALTER TABLE answers ADD COLUMN topic TEXT DEFAULT ''")
        await db.commit()

async def user_row(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM users WHERE uid=?", (uid,))).fetchone()
        return dict(row) if row else None

async def touch_user(user):
    name = " ".join(x for x in [user.first_name, user.last_name] if x)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(uid,name,username) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET name=excluded.name,username=excluded.username",
                         (user.id, name, user.username or ""))
        await db.commit()

def today(): return datetime.now(TZ).date().isoformat()

def shuffle_q(q, seed=None):
    x = dict(q); pairs = list(enumerate(q["o"]))
    random.Random(seed).shuffle(pairs)
    x["o"] = [p[1] for p in pairs]; x["c"] = next(i for i,p in enumerate(pairs) if p[0] == q["c"])
    return x

def category_questions(category):
    if category == "aralash":
        pool = QUIZ_BANK
    elif category == "kitobdan":
        pool = BOOK_BANK
    else:
        topic = {"it": "IT", "matem": "Matem", "ingliz": "Ingliz", "mantiqiy": "Mantiq", "islomiy": "Diniy"}.get(category)
        pool = [q for q in QUIZ_BANK if q.get("t") == topic]
    return sorted(pool, key=lambda q: q["q"])

async def run_category(uid, chat_id, category, start=0):
    if category == "viktorina":
        return await run_daily(uid, chat_id, None)
    pool = category_questions(category)
    if not pool:
        return await bot.send_message(chat_id, "Bu kategoriyada hozircha savollar yo'q.", reply_markup=MENU)
    start %= len(pool)
    await bot.send_message(chat_id, f"{CATEGORY_LABELS[category]} kategoriyasi: {min(5, len(pool))} ta savol.")
    await send_question(chat_id, uid, f"category:{category}", start, 0, shuffle_q(pool[start], start), min(5, len(pool)))

async def daily_questions():
    d = today()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT questions FROM daily_quiz WHERE day=?", (d,))).fetchone()
    if row: return json.loads(row[0])
    if AI_TOKEN:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=AI_TOKEN, base_url="https://openrouter.ai/api/v1")
            prompt = ('Create exactly 15 accurate Uzbek quiz questions as JSON object {"questions":[...]}. '
              'Each item has q, o (4 strings), c (correct index 0..3), t. Exact topic counts: IT 6, Liderlik 3, Diniy 2, Kino 2, Mantiq 2.')
            response = await client.chat.completions.create(model="openai/gpt-4o-mini", messages=[{"role":"user","content":prompt}], response_format={"type":"json_object"})
            qs = json.loads(response.choices[0].message.content)["questions"]
            counts = {t: sum(q.get("t") == t for q in qs) for t in ["IT","Liderlik","Diniy","Kino","Mantiq"]}
            if len(qs) == 15 and counts == {"IT":6,"Liderlik":3,"Diniy":2,"Kino":2,"Mantiq":2}:
                random.shuffle(qs)
                qs = [shuffle_q(q, random.randrange(1_000_000)) for q in qs]
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("INSERT OR REPLACE INTO daily_quiz VALUES(?,?)", (d,json.dumps(qs,ensure_ascii=False))); await db.commit()
                return qs
        except Exception:
            logging.exception("AI quiz generation failed; using bank fallback")
    # Keep the bot usable when AI token/provider is temporarily unavailable.
    plan = {"IT":6,"Liderlik":3,"Diniy":2,"Kino":2,"Mantiq":2}
    out=[]
    for topic,count in plan.items():
        pool=[q for q in QUIZ_BANK if q.get("t")==topic]
        out.extend(random.sample(pool,min(count,len(pool))))
    random.shuffle(out)
    out=[shuffle_q(q,random.randrange(1_000_000)) for q in out]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO daily_quiz VALUES(?,?)",(d,json.dumps(out,ensure_ascii=False))); await db.commit()
    return out

async def send_question(chat_id, uid, mode, stage, idx, q, total):
    question = q["q"]
    if mode == "book" or mode == "category:kitobdan":
        question += " (Ilm olish sirlari kitobidan)"
    poll = await bot.send_poll(chat_id, question, q["o"], type=PollType.QUIZ, correct_option_id=q["c"],
       is_anonymous=False, open_period=30)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO polls VALUES(?,?,?,?,?,?,?,?,?,?)",
          (poll.poll.id,uid,chat_id,mode,stage,idx,q["q"],json.dumps(q["o"],ensure_ascii=False),q["c"],total)); await db.commit()

async def answer_poll(uid, poll_id, correct):
    async with aiosqlite.connect(DB_PATH) as db:
        row=await (await db.execute("SELECT * FROM polls WHERE poll_id=? AND uid=?",(poll_id,uid))).fetchone()
        if not row: return None
        keys=["poll_id","uid","chat_id","mode","stage","idx","question","options","correct_idx","total"]
        rec=dict(zip(keys,row))
        await db.execute("DELETE FROM polls WHERE poll_id=?",(poll_id,)); await db.commit()
    if rec['mode']=='daily':
        qs=await daily_questions(); topic=qs[rec['idx']].get('t','')
    elif rec['mode']=='book': topic='Kitob'
    elif rec['mode'].startswith('category:'):
        category=rec['mode'].split(':',1)[1]
        pool=category_questions(category)
        topic='Kitob' if category=='kitobdan' else pool[(rec['stage']+rec['idx'])%len(pool)].get('t','')
    else:
        order=sorted(QUIZ_BANK,key=lambda q:q['q']); topic=order[(rec['stage']+rec['idx'])%len(order)].get('t','')
    async with aiosqlite.connect(DB_PATH) as db:
        day=today()
        cur=await db.execute("INSERT OR IGNORE INTO answers(uid,day,mode,stage,idx,correct,topic) VALUES(?,?,?,?,?,?,?)",(uid,day,rec['mode'],rec['stage'],rec['idx'],int(correct),topic))
        if cur.rowcount:
            await db.execute("UPDATE users SET answered=answered+1,correct=correct+? WHERE uid=?",(int(correct),uid))
        await db.commit()
    return rec

async def finish_daily(uid, chat_id, qs):
    d=today()
    async with aiosqlite.connect(DB_PATH) as db:
        score=(await (await db.execute("SELECT COALESCE(SUM(correct),0) FROM answers WHERE uid=? AND day=? AND mode='daily'",(uid,d))).fetchone())[0]
        row=await (await db.execute("SELECT streak,last_daily,days FROM users WHERE uid=?",(uid,))).fetchone()
        streak=(row[0]+1 if row[1]==(date.fromisoformat(d)-timedelta(days=1)).isoformat() else 1)
        await db.execute("UPDATE users SET streak=?,last_daily=?,days=? WHERE uid=?",(streak,d,row[2]+1,uid)); await db.commit()
    pct=round(score/len(qs)*100) if qs else 0
    await bot.send_message(chat_id,f"🏁 <b>Viktorina yakunlandi!</b>\n\nNatija: <b>{score}/{len(qs)}</b> ({pct}%)\n🔥 Ketma-ket kunlar: <b>{streak}</b>",reply_markup=MENU)

async def run_daily(uid, chat_id, user):
    qs=await daily_questions(); d=today()
    async with aiosqlite.connect(DB_PATH) as db:
        done=await (await db.execute("SELECT COUNT(*) FROM answers WHERE uid=? AND day=? AND mode='daily'",(uid,d))).fetchone()
        if done[0]>=len(qs):
            score=(await (await db.execute("SELECT COALESCE(SUM(correct),0) FROM answers WHERE uid=? AND day=? AND mode='daily'",(uid,d))).fetchone())[0]
            return await bot.send_message(chat_id,f"✅ Bugungi viktorinani ishlagansiz: {score}/{len(qs)}",reply_markup=MENU)
    idx=done[0]
    if idx==0: await bot.send_message(chat_id,"🧠 <b>Kunlik viktorina</b>\n15 savol, bugungi natijangiz reytingga qo'shiladi.")
    await send_question(chat_id,uid,"daily",0,idx,qs[idx],len(qs))

def book_questions(fasl,uid,attempt=0):
    pool=[q for q in BOOK_BANK if q.get('f')==fasl]
    random.Random(f"{uid}:{fasl}:{attempt}").shuffle(pool)
    qs=pool[:min(8,len(pool))]
    return [shuffle_q(q,f"{uid}:{fasl}:{attempt}:{i}") for i,q in enumerate(qs)]

async def run_book(uid,chat_id,fasl):
    u=await user_row(uid); current=min(u['kitob']+1,14)
    if fasl>current: return await bot.send_message(chat_id,f"🔒 Avval {current}-faslni yakunlang.")
    qs=book_questions(fasl,uid,1 if fasl<=u['kitob'] else 0)
    if not qs: return await bot.send_message(chat_id,"Bu fasl uchun savol topilmadi.")
    async with aiosqlite.connect(DB_PATH) as db:
        done=await (await db.execute("SELECT COUNT(*) FROM answers WHERE uid=? AND day=? AND mode='book' AND stage=?",(uid,today(),fasl))).fetchone()
    idx=done[0]
    if idx>=len(qs):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM answers WHERE uid=? AND day=? AND mode='book' AND stage=?",(uid,today(),fasl)); await db.commit()
        idx=0
    await bot.send_message(chat_id,f"📖 <b>{fasl}-fasl. {FASL_NAMES[fasl-1]}</b>\nSavol {idx+1}/{len(qs)}")
    await send_question(chat_id,uid,"book",fasl,idx,qs[idx],len(qs))

async def book_menu(chat_id,uid):
    u=await user_row(uid); done=u['kitob']; stage=min(done+1,14)
    kb=InlineKeyboardBuilder(); kb.button(text=f"▶️ {stage}-faslni boshlash",callback_data=f"book:{stage}")
    if done: kb.button(text="🔁 O'tgan faslni takrorlash",callback_data=f"book:{done}")
    kb.adjust(1)
    await bot.send_message(chat_id,f"📖 <b>Ilm olish sirlari marafoni</b>\n\n{done}/14 fasl yakunlangan.\nHozir: <b>{FASL_NAMES[stage-1]}</b>",reply_markup=kb.as_markup())

async def advance(rec,correct):
    uid,chat_id,mode,stage,idx,total=rec['uid'],rec['chat_id'],rec['mode'],rec['stage'],rec['idx'],rec['total']
    if mode=='daily':
        qs=await daily_questions()
        if idx+1>=total: return await finish_daily(uid,chat_id,qs)
        return await send_question(chat_id,uid,mode,stage,idx+1,qs[idx+1],total)
    if mode=='book':
        qs=book_questions(stage,uid,1 if stage<=(await user_row(uid))['kitob'] else 0)
        if idx+1>=len(qs):
            async with aiosqlite.connect(DB_PATH) as db:
                score=(await (await db.execute("SELECT COALESCE(SUM(correct),0) FROM answers WHERE uid=? AND day=? AND mode='book' AND stage=?",(uid,today(),stage))).fetchone())[0]
                u=await (await db.execute("SELECT kitob FROM users WHERE uid=?",(uid,))).fetchone()
                if stage>u[0]: await db.execute("UPDATE users SET kitob=?,kitob_ball=kitob_ball+?,kitob_javob=kitob_javob+? WHERE uid=?",(stage,score,total,uid))
                await db.commit()
            kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Keyingi fasl ▶️",callback_data=f"book:{min(stage+1,14)}")],[InlineKeyboardButton(text="📊 Natijalarim",callback_data="stats")]])
            return await bot.send_message(chat_id,f"✅ <b>{stage}-fasl yakunlandi</b>\nNatija: {score}/{total}\nProgress: {max(stage,(await user_row(uid))['kitob'])}/14",reply_markup=kb)
        return await send_question(chat_id,uid,mode,stage,idx+1,qs[idx+1],total)
    if mode.startswith('category:'):
        category=mode.split(':',1)[1]; pool=category_questions(category)
        if idx+1>=total:
            next_start=(stage+total)%len(pool)
            kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Keyingi 5 ta ▶️",callback_data=f"category-next:{category}:{next_start}")]])
            return await bot.send_message(chat_id,"✅ Bu tur yakunlandi.",reply_markup=kb)
        next_idx=(stage+idx+1)%len(pool)
        return await send_question(chat_id,uid,mode,stage,idx+1,shuffle_q(pool[next_idx],next_idx),total)
    u=await user_row(uid); order=sorted(QUIZ_BANK,key=lambda q:q['q'])
    if idx+1>=5:
        nextidx=(stage+5)%len(order)
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE users SET seq=? WHERE uid=?",(nextidx,uid)); await db.commit()
        return await bot.send_message(chat_id,"✅ 5 ta savol tugadi.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Keyingi 5 ta",callback_data="seq")]]))
    q=shuffle_q(order[(stage+idx+1)%len(order)],stage+idx+1)
    await send_question(chat_id,uid,'practice',stage,idx+1,q,5)

@dp.poll_answer()
async def poll_answer_handler(event: PollAnswer):
    async with aiosqlite.connect(DB_PATH) as db:
        row=await (await db.execute("SELECT correct_idx FROM polls WHERE poll_id=? AND uid=?",(event.poll_id,event.user.id))).fetchone()
    if not row: return
    selected=event.option_ids[0] if event.option_ids else -1
    correct=selected==row[0]
    rec=await answer_poll(event.user.id,event.poll_id,correct)
    if not rec: return
    await advance(rec,correct)

@dp.poll()
async def poll_closed(poll: Poll):
    if poll.is_closed:
        await asyncio.sleep(1)
        async with aiosqlite.connect(DB_PATH) as db:
            row=await (await db.execute("SELECT * FROM polls WHERE poll_id=?",(poll.id,))).fetchone()
        if row:
            rec=dict(zip(["poll_id","uid","chat_id","mode","stage","idx","question","options","correct_idx","total"],row))
            # unanswered poll: count as wrong and continue
            await answer_poll(rec['uid'],poll.id,False)
            await bot.send_message(rec['chat_id'],"⏱ Vaqt tugadi.")
            await advance(rec,False)

async def stats(chat_id,uid):
    u=await user_row(uid)
    if not u or not u['answered']: return await bot.send_message(chat_id,"Hali javoblar yo'q. Kunlik viktorinadan boshlang!",reply_markup=MENU)
    pct=round(u['correct']/u['answered']*100)
    async with aiosqlite.connect(DB_PATH) as db:
        topics=await (await db.execute("SELECT topic,SUM(correct),COUNT(*) FROM answers WHERE uid=? AND topic<>'' GROUP BY topic ORDER BY topic",(uid,))).fetchall()
    topic_text="\n".join(f"{TOPICS.get(t,t)}: {c}/{n} ({round(c/n*100)}%)" for t,c,n in topics)
    await bot.send_message(chat_id,f"📊 <b>Natijalaringiz</b>\n\nJavoblar: {u['answered']}\nTo'g'ri: {u['correct']} ({pct}%)\n🔥 Streak: {u['streak']} kun\n📅 Kunlar: {u['days']}\n📖 Kitob: {u['kitob']}/14 ({u['kitob_ball']}/{u['kitob_javob']})\n\n<b>Mavzular:</b>\n{topic_text}",reply_markup=MENU)

async def leaderboard(chat_id, day=None):
    d=day or today()
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT u.name,u.username,SUM(a.correct),COUNT(*) FROM answers a JOIN users u ON a.uid=u.uid WHERE a.day=? AND a.mode='daily' GROUP BY a.uid HAVING COUNT(*)=15 ORDER BY SUM(a.correct) DESC,COUNT(*) DESC LIMIT 10",(d,))).fetchall()
    text=f"🏆 <b>Kunlik viktorina hisoboti</b> · {d}\n\n"
    text += "\n".join(f"{i}. {'@'+r[1] if r[1] else r[0]} — {r[2]}/{r[3]}" for i,r in enumerate(rows,1)) if rows else "Bugun hali yakunlangan natijalar yo'q."
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Botda viktorinani boshlash",url=f"https://t.me/{BOT_USERNAME}?start=kunlik")]])
    await bot.send_message(chat_id,text,reply_markup=kb)

def category_top_five(rows):
    totals={}
    for mode,uid,name,username,correct,answered in rows:
        if mode=='practice': category='aralash'
        elif mode.startswith('category:'): category=mode.split(':',1)[1]
        else: continue
        if category not in CATEGORY_LABELS or category=='viktorina': continue
        key=(category,uid)
        current=totals.setdefault(key,[name,username,0,0])
        current[2]+=correct; current[3]+=answered
    rankings={}
    for (category,uid),(name,username,correct,answered) in totals.items():
        if answered<5: continue
        rankings.setdefault(category,[]).append((correct/answered,correct,answered,name,username,uid))
    return {category: sorted(entries,key=lambda row:(row[0],row[1],row[2],row[5]),reverse=True)[:5]
            for category,entries in rankings.items()}

async def category_leaderboard(chat_id, day=None):
    d=day or today()
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT a.mode,u.uid,u.name,u.username,SUM(a.correct),COUNT(*) FROM answers a JOIN users u ON u.uid=a.uid WHERE a.day=? AND (a.mode='practice' OR a.mode LIKE 'category:%') GROUP BY a.mode,a.uid",(d,))).fetchall()
    winners=category_top_five(rows)
    lines=[]
    for category,label in CATEGORY_LABELS.items():
        if category=='viktorina' or category not in winners: continue
        lines.append(f"<b>{label}</b>")
        for position,(_,correct,answered,name,username,_) in enumerate(winners[category],1):
            participant='@'+username if username else name
            lines.append(f"{position}. {participant} — {correct}/{answered} ({round(correct/answered*100)}%)")
    text=f"📊 <b>Kategoriyalar bo'yicha kun g'oliblari</b> · {d}\n\n"
    text += "\n".join(lines) if lines else "Bugun kategoriyalarda yakunlangan mashqlar yo'q."
    await bot.send_message(chat_id,text)

async def remind_users():
    d=today()
    async with aiosqlite.connect(DB_PATH) as db:
        users=await (await db.execute("SELECT u.uid FROM users u LEFT JOIN answers a ON a.uid=u.uid AND a.day=? AND a.mode='daily' GROUP BY u.uid HAVING COUNT(a.idx)<15",(d,))).fetchall()
    for offset in range(0,len(users),20):
        for (uid,) in users[offset:offset+20]:
            try: await bot.send_message(uid,"🧠 Bugungi viktorina kutmoqda. Davom etish uchun tugmani bosing.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Viktorinani ochish",url=f"https://t.me/{BOT_USERNAME}?start=kunlik")]]))
            except Exception: logging.info("Reminder skipped for %s",uid)
        await asyncio.sleep(1)

async def book_leaderboard(chat_id):
    d=today()
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT u.name,u.username,a.stage,SUM(a.correct),COUNT(*) FROM answers a JOIN users u ON u.uid=a.uid WHERE a.day=? AND a.mode='book' GROUP BY a.uid,a.stage ORDER BY SUM(a.correct) DESC LIMIT 50",(d,))).fetchall()
    if not rows: return await bot.send_message(chat_id,"Bugun kitob marafonida natijalar yo'q.")
    await bot.send_message(chat_id,"📖 <b>Bugungi kitob marafoni reytingi</b>\n\n"+"\n".join(f"{i}. {'@'+r[1] if r[1] else r[0]} — {r[3]}/{r[4]} ({r[2]}-fasl)" for i,r in enumerate(rows,1)))

@dp.message(CommandStart())
async def start(m:Message):
    await touch_user(m.from_user)
    arg=(m.text or "").split(maxsplit=1)
    if len(arg)>1 and arg[1].startswith("fasl"):
        try: return await run_book(m.from_user.id,m.chat.id,int(arg[1][4:]))
        except ValueError: pass
    if len(arg)>1 and arg[1]=="kitob": return await book_menu(m.chat.id,m.from_user.id)
    if len(arg)>1 and arg[1]=="kunlik": return await run_daily(m.from_user.id,m.chat.id,m.from_user)
    await m.answer(f"Salom, {m.from_user.first_name}! Ustozim Nurim bilim sinovi botiga xush kelibsiz.\n\n🧠 Kunlik viktorina\n📚 O'z tezligimda mashq\n📖 14 bosqichli kitob marafoni\n📊 Natijalar va reyting",reply_markup=MENU)

@dp.message(F.text=="🧠 Kunlik viktorina")
async def daily(m:Message): await touch_user(m.from_user); await run_daily(m.from_user.id,m.chat.id,m.from_user)

@dp.message(F.text=="📖 Kitob marafoni")
async def book(m:Message): await touch_user(m.from_user); await book_menu(m.chat.id,m.from_user.id)

@dp.message(F.text=="📚 Kategoriyalar")
async def categories(m:Message):
    kb=InlineKeyboardBuilder()
    for key,label in CATEGORY_LABELS.items(): kb.button(text=label,callback_data=f"category:{key}")
    kb.adjust(2)
    await m.answer("Savol kategoriyasini tanlang:",reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("category:"))
async def category_cb(c:CallbackQuery):
    await c.answer()
    await run_category(c.from_user.id,c.message.chat.id,c.data.split(':',1)[1])

@dp.callback_query(F.data.startswith("category-next:"))
async def category_next_cb(c:CallbackQuery):
    await c.answer()
    _,category,start=c.data.split(':')
    await run_category(c.from_user.id,c.message.chat.id,category,int(start))

@dp.message(F.text=="📚 O'z tezligimda")
async def practice(m:Message):
    await touch_user(m.from_user); u=await user_row(m.from_user.id); order=sorted(QUIZ_BANK,key=lambda q:q['q']); idx=u['seq']%len(order)
    await send_question(m.chat.id,m.from_user.id,'practice',idx,0,shuffle_q(order[idx],idx),5)

@dp.callback_query(F.data=="seq")
async def practice_cb(c:CallbackQuery):
    await c.answer(); u=await user_row(c.from_user.id); order=sorted(QUIZ_BANK,key=lambda q:q['q']); idx=u['seq']%len(order)
    await send_question(c.message.chat.id,c.from_user.id,'practice',idx,0,shuffle_q(order[idx],idx),5)

@dp.message(Command("mashq"))
async def practice_cmd(m:Message): await practice(m)

@dp.message(Command("kitob"))
async def book_cmd(m:Message): await book(m)

@dp.message(Command("fasl"))
async def fasl_cmd(m:Message):
    try: n=int((m.text or '').split()[1]); await run_book(m.from_user.id,m.chat.id,n)
    except (ValueError,IndexError): await book_menu(m.chat.id,m.from_user.id)

@dp.message(F.text=="📊 Natijalarim")
async def stats_cmd(m:Message): await stats(m.chat.id,m.from_user.id)

@dp.callback_query(F.data=="stats")
async def stats_cb(c:CallbackQuery): await c.answer(); await stats(c.message.chat.id,c.from_user.id)

@dp.message(F.text=="🏆 Reyting")
async def rank(m:Message): await leaderboard(m.chat.id)

@dp.message(Command("reyting"))
async def rank_cmd(m:Message): await leaderboard(m.chat.id)

@dp.callback_query(F.data.startswith("book:"))
async def book_cb(c:CallbackQuery): await c.answer(); await run_book(c.from_user.id,c.message.chat.id,int(c.data.split(':')[1]))

@dp.message(F.text=="ℹ️ Yordam")
@dp.message(Command("help"))
@dp.message(Command("yordam"))
async def help_msg(m:Message): await m.answer("Buyruqlar: /kunlik /mashq /kitob /fasl 1 /natija /reyting\nHar kuni yechilgan savollar reytingga tushadi.",reply_markup=MENU)

@dp.message(Command("kunlik"))
async def daily_cmd(m:Message): await daily(m)

@dp.message(Command("natija"))
async def stats_slash(m:Message): await stats(m.chat.id,m.from_user.id)

@dp.message(Command("stats"))
async def admin_stats(m:Message):
    if m.from_user.id!=ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        users=(await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
    await m.answer(f"Admin: {users} foydalanuvchi, {len(QUIZ_BANK)} bazaviy savol.")

@dp.message(Command("reset"))
async def reset(m:Message):
    if m.from_user.id!=ADMIN_ID: return
    parts=(m.text or '').split(); target=int(parts[1]) if len(parts)>1 and parts[1].isdigit() else m.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM answers WHERE uid=? AND day=?",(target,today()))
        await db.execute("UPDATE users SET seq=0,kitob=0,kitob_ball=0,kitob_javob=0 WHERE uid=?",(target,)); await db.commit()
    await m.answer(f"{target} foydalanuvchining bugungi holati va marafon progressi tozalandi.")

@dp.message(Command("sinov"))
async def test_admin(m:Message):
    if m.from_user.id!=ADMIN_ID: return
    args=(m.text or '').split()
    if len(args)>1 and args[1]=='reyting': await book_leaderboard(CHANNEL_ID)
    elif len(args)>1 and args[1]=='fasl': await post_fasl()
    else: await m.answer("/sinov fasl yoki /sinov reyting")

async def post_fasl():
    day=(date.today()-KITOB_START).days
    if 0<=day<14:
        url=f"https://t.me/{BOT_USERNAME}?start=fasl{day+1}"
        await bot.send_message(CHANNEL_ID,f"📖 <b>{day+1}-fasl: {FASL_NAMES[day]}</b>\n\nBugungi kitob sinovida qatnashing.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Sinovdan o'tish",url=url)]]))

async def scheduler():
    sent=set()
    while True:
        now=datetime.now(TZ); key=now.strftime('%Y-%m-%d %H:%M')
        if now.hour==0 and now.minute==0:
            try: await daily_questions()
            except Exception: logging.exception("quiz generation")
        if now.hour==21 and now.minute==0 and key not in sent:
            sent.add(key)
            try:
                await post_fasl(); await remind_users()
            except Exception: logging.exception("reminder")
        if now.hour==23 and now.minute==30 and key not in sent:
            sent.add(key)
            try: await leaderboard(GROUP_ID)
            except Exception: logging.exception("group leaderboard")
            try: await category_leaderboard(GROUP_ID)
            except Exception: logging.exception("category leaderboard")
        if now.hour==23 and now.minute==30 and key not in sent:
            sent.add(key)
            try: await book_leaderboard(CHANNEL_ID)
            except Exception: logging.exception("channel leaderboard")
        await asyncio.sleep(20)

async def main():
    global bot
    await init_db()
    bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([BotCommand(command="kunlik",description="Bugungi viktorina"),BotCommand(command="kitob",description="Kitob marafoni"),BotCommand(command="mashq",description="O'z tezligimda"),BotCommand(command="natija",description="Natijalarim"),BotCommand(command="reyting",description="Bugungi reyting"),BotCommand(command="yordam",description="Yordam")])
    asyncio.create_task(scheduler())
    logging.info("Bot polling bilan ishga tushdi")
    await dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types())

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

