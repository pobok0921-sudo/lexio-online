# -*- coding: utf-8 -*-
"""
렉시오(Lexio) 온라인 멀티플레이 서버
- 계정 가입/로그인 (비밀번호 PBKDF2 해시 저장)
- 방 생성/입장 로비
- 서버 권위(authoritative) 게임 엔진: 부정 방지 + 실시간 동기화
- WebSocket 으로 모든 접속자에게 상태 브로드캐스트
실행:  python server.py   (기본 http://0.0.0.0:8000)
"""
import asyncio, json, os, secrets, hashlib, random, time
from aiohttp import web, WSMsgType

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.path.join(HERE, "data")
USERS_FILE = os.path.join(DATA, "users.json")
os.makedirs(DATA, exist_ok=True)

PORT = int(os.environ.get("PORT", "8000"))

# ================= 계정 저장 =================
def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_users(u):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USERS_FILE)

USERS = load_users()

# ================= 계정 영구저장 (선택: PostgreSQL) =================
# DATABASE_URL 환경변수가 있으면 Postgres 사용(클라우드 상시 운영 시 계정 유지),
# 없으면 기존처럼 data/users.json 파일 사용(로컬).
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = os.environ.get("ADMIN_ID", "admin")   # 로그인 화면에서 관리자 아이디
ADMIN_PW = os.environ.get("ADMIN_PW", "1234")    # 관리자 비밀번호
GAMES_FILE = os.path.join(DATA, "games.json")
_pool = None

def _load_games():
    if os.path.exists(GAMES_FILE):
        try:
            with open(GAMES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _save_games(g):
    tmp = GAMES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False)
    os.replace(tmp, GAMES_FILE)

async def db_init():
    global _pool
    if not DATABASE_URL:
        print("DB: 로컬 파일 모드 (data/users.json)")
        return
    try:
        import asyncpg, ssl as _ssl
        dsn = DATABASE_URL.split("?")[0]            # sslmode 등 쿼리 제거
        ctx = _ssl.create_default_context()
        # statement_cache_size=0 : Neon/PgBouncer 풀러에서 prepared statement 충돌 방지
        _pool = await asyncpg.create_pool(dsn=dsn, ssl=ctx, min_size=1, max_size=5,
                                          statement_cache_size=0)
        async with _pool.acquire() as c:
            await c.execute("CREATE TABLE IF NOT EXISTS users("
                            "username text PRIMARY KEY, salt text, hash text, "
                            "wins int DEFAULT 0, games int DEFAULT 0, created timestamptz DEFAULT now())")
            await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS games int DEFAULT 0")
            await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created timestamptz DEFAULT now()")
            await c.execute("CREATE TABLE IF NOT EXISTS game_log("
                            "id serial PRIMARY KEY, ts timestamptz DEFAULT now(), "
                            "room text, winner text, detail jsonb)")
            rows = await c.fetch("SELECT username, salt, hash, wins, games, created FROM users")
        USERS.clear()
        for r in rows:
            USERS[r["username"]] = {"salt": r["salt"], "hash": r["hash"], "wins": r["wins"],
                                    "games": r["games"] or 0, "created": str(r["created"])[:19]}
        print(f"DB: PostgreSQL 연결됨, 계정 {len(USERS)}개 로드")
    except Exception as e:
        _pool = None
        print("DB: 연결 실패 → 파일 모드로 대체:", e)

async def persist_user(uid):
    if uid not in USERS:
        return
    u = USERS[uid]
    if _pool:
        try:
            async with _pool.acquire() as c:
                await c.execute(
                    "INSERT INTO users(username,salt,hash,wins,games) VALUES($1,$2,$3,$4,$5) "
                    "ON CONFLICT(username) DO UPDATE SET wins=EXCLUDED.wins, games=EXCLUDED.games",
                    uid, u["salt"], u["hash"], u.get("wins", 0), u.get("games", 0))
        except Exception as e:
            print("DB persist 오류:", e)
    else:
        save_users(USERS)

async def record_game(room_name, winner_name, detail):
    """라운드 결과 1건을 영구 기록(계속 누적)."""
    if _pool:
        try:
            async with _pool.acquire() as c:
                await c.execute("INSERT INTO game_log(room,winner,detail) VALUES($1,$2,$3)",
                                room_name, winner_name, json.dumps(detail, ensure_ascii=False))
        except Exception as e:
            print("record_game DB 오류:", e)
    else:
        try:
            g = _load_games()
            g.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "room": room_name,
                      "winner": winner_name, "detail": detail})
            _save_games(g[-1000:])
        except Exception as e:
            print("record_game 파일 오류:", e)

async def get_games_recent(n=200):
    if _pool:
        try:
            async with _pool.acquire() as c:
                rows = await c.fetch("SELECT ts, room, winner, detail FROM game_log ORDER BY id DESC LIMIT $1", n)
            out = []
            for r in rows:
                d = r["detail"]
                if isinstance(d, str):
                    try: d = json.loads(d)
                    except Exception: d = []
                out.append({"ts": str(r["ts"])[:19], "room": r["room"], "winner": r["winner"], "detail": d})
            return out
        except Exception as e:
            print("games fetch 오류:", e)
            return []
    else:
        return list(reversed(_load_games()))[:n]

async def admin_data():
    users = sorted(
        [{"name": k, "wins": v.get("wins", 0), "games": v.get("games", 0),
          "created": str(v.get("created", ""))[:19]} for k, v in USERS.items()],
        key=lambda u: (-u["games"], -u["wins"], u["name"]))
    games = await get_games_recent(200)
    return {"users": users, "games": games, "online": len(USER_WS), "rooms": len(ROOMS),
            "storage": "PostgreSQL(영구)" if _pool else "로컬파일(임시)"}

def hash_pw(pw, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200000)
    return salt, h.hex()

def verify_pw(pw, salt, hexhash):
    _, h = hash_pw(pw, salt)
    return secrets.compare_digest(h, hexhash)

# ================= 렉시오 엔진 =================
COLORS = ["cloud", "star", "moon", "sun"]  # 0<1<2<3
# 모든 인원에서 숫자 1~15 전체 사용 (덱 60장: 2인 30/3인 20/4인 15/5인 12장씩)
PLAYER_MAX = {2: 15, 3: 15, 4: 15, 5: 15}  # 인원 -> 최대숫자

# AI 플레이어 이름(연예인)
AI_NAMES = ["유재석", "강호동", "박명수", "김종국", "하하", "이광수", "아이유", "이효리",
            "손흥민", "김연아", "차은우", "수지", "카리나", "제니", "장원영", "임영웅",
            "전지현", "공유", "박보검", "유인나"]

def nrank(n, maxnum):
    if n >= 3: return n - 3
    if n == 1: return maxnum - 2
    return maxnum - 1  # 2

def crank(c): return COLORS.index(c)

def allowed_straights(maxnum):
    res = []
    for k in range(3, maxnum - 4 + 1):
        res.append([k, k+1, k+2, k+3, k+4])
    res.append([maxnum-3, maxnum-2, maxnum-1, maxnum, 1])
    res.append([2, 3, 4, 5, 6])
    res.append([1, 2, 3, 4, 5])
    return set(",".join(map(str, sorted(a))) for a in res)

def build_deck(maxnum):
    nums = list(range(3, maxnum + 1)) + [1, 2]
    deck = []
    i = 0
    for n in nums:
        for c in COLORS:
            deck.append({"num": n, "color": c, "id": i}); i += 1
    return deck

def classify(tiles, maxnum, strset):
    c = len(tiles)
    if c == 1:
        t = tiles[0]
        return {"count": 1, "cat": "single", "key": [nrank(t["num"], maxnum), crank(t["color"])]}
    if c == 2:
        if tiles[0]["num"] != tiles[1]["num"]: return None
        hi = max(crank(tiles[0]["color"]), crank(tiles[1]["color"]))
        return {"count": 2, "cat": "pair", "key": [nrank(tiles[0]["num"], maxnum), hi]}
    if c == 3:
        if not (tiles[0]["num"] == tiles[1]["num"] == tiles[2]["num"]): return None
        return {"count": 3, "cat": "triple", "key": [nrank(tiles[0]["num"], maxnum)]}
    if c == 5:
        return classify_made(tiles, maxnum, strset)
    return None

def classify_made(tiles, maxnum, strset):
    nums = [t["num"] for t in tiles]
    cnt = {}
    for n in nums: cnt[n] = cnt.get(n, 0) + 1
    vals = sorted(cnt.values(), reverse=True)
    same_color = all(t["color"] == tiles[0]["color"] for t in tiles)
    straight = ",".join(map(str, sorted(nums))) in strset
    ranks_desc = sorted([nrank(t["num"], maxnum) for t in tiles], reverse=True)
    top = max(tiles, key=lambda t: nrank(t["num"], maxnum))
    if straight and same_color:
        return {"count": 5, "cat": "straightflush", "key": [5] + ranks_desc + [crank(top["color"])]}
    if vals[0] == 4:
        q = [n for n in cnt if cnt[n] == 4][0]
        return {"count": 5, "cat": "fourkind", "key": [4, nrank(q, maxnum)]}
    if vals[0] == 3 and len(vals) > 1 and vals[1] == 2:
        tr = [n for n in cnt if cnt[n] == 3][0]
        return {"count": 5, "cat": "fullhouse", "key": [3, nrank(tr, maxnum)]}
    if same_color:
        # 플러쉬: 가장 높은 숫자 우선(숫자2 포함이 최강), 숫자 동일 시 색상으로 최종 비교
        return {"count": 5, "cat": "flush", "key": [2] + ranks_desc + [crank(tiles[0]["color"])]}
    if straight:
        return {"count": 5, "cat": "straight", "key": [1] + ranks_desc + [crank(top["color"])]}
    return None

def key_cmp(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    return len(a) - len(b)

def beats(combo, cur):
    if not combo: return False
    if not cur: return True
    if combo["count"] != cur["count"]: return False
    return key_cmp(combo["key"], cur["key"]) > 0

def combinations(arr, k):
    res = []
    def go(start, pick):
        if len(pick) == k:
            res.append(pick[:]); return
        for i in range(start, len(arr)):
            pick.append(arr[i]); go(i + 1, pick); pick.pop()
    go(0, [])
    return res

def enumerate_plays(hand, count, maxnum, strset):
    out = []
    for t in combinations(hand, count):
        c = classify(t, maxnum, strset)
        if c: out.append({"tiles": t, "combo": c})
    return out

def sort_hand(hand, maxnum):
    hand.sort(key=lambda t: (nrank(t["num"], maxnum), crank(t["color"])))

COMBO_KR = {"single":"싱글","pair":"페어","triple":"트리플","straight":"스트레이트",
            "flush":"플러쉬","fullhouse":"풀하우스","fourkind":"포카드","straightflush":"스트레이트플러쉬"}
CNAME = {"cloud":"구름","star":"별","moon":"달","sun":"해"}

# ================= AI (난이도: easy / normal / hard) =================
def _hand_counts(hand):
    c = {}
    for t in hand: c[t["num"]] = c.get(t["num"], 0) + 1
    return c

def _has_top(tiles):
    return any(t["num"] in (1, 2) for t in tiles)

def _c3_in(tiles):
    return any(t["num"] == 3 and t["color"] == "cloud" for t in tiles)

def _all_lead_cands(hand, maxnum, strset):
    cands = []
    for n in (1, 2, 3):
        cands += enumerate_plays(hand, n, maxnum, strset)
    if len(hand) >= 5:
        cands += enumerate_plays(hand, 5, maxnum, strset)
    return cands

def _single_play(t, maxnum, strset):
    return {"tiles": [t], "combo": classify([t], maxnum, strset)}

def _lowest_tile(pool, maxnum):
    return min(pool, key=lambda t: (nrank(t["num"], maxnum), crank(t["color"])))

def ai_params(grade):
    """1등급(가장 약함) ~ 5등급(가장 강함)"""
    table = {
        1: {"pass_rand": 0.55, "random_play": True,  "save_top": None,    "smart_lead": "weak",   "lead_rand": 0.85, "go_out": False},
        2: {"pass_rand": 0.30, "random_play": True,  "save_top": None,    "smart_lead": "weak",   "lead_rand": 0.45, "go_out": False},
        3: {"pass_rand": 0.0,  "random_play": False, "save_top": "soft",  "smart_lead": "normal", "lead_rand": 0.0,  "go_out": True},
        4: {"pass_rand": 0.0,  "random_play": False, "save_top": "soft",  "smart_lead": "hard",   "lead_rand": 0.0,  "go_out": True},
        5: {"pass_rand": 0.0,  "random_play": False, "save_top": "hard",  "smart_lead": "hard",   "lead_rand": 0.0,  "go_out": True},
    }
    try:
        g = int(grade)
    except (TypeError, ValueError):
        g = 3
    return table.get(g, table[3])

def ai_lead(grade, hand, first_move, maxnum, strset):
    p = ai_params(grade)
    must_c3 = False  # 선도 구름3을 꼭 내지 않아도 됨(자유 시작)
    # 한 번에 전부 털어 승리 가능하면 (중급 이상)
    if p["go_out"] and len(hand) in (1, 2, 3, 5):
        full = classify(hand, maxnum, strset)
        if full and ((not must_c3) or _c3_in(hand)):
            return {"tiles": hand[:], "combo": full}

    cands = _all_lead_cands(hand, maxnum, strset)
    if must_c3:
        cands = [c for c in cands if _c3_in(c["tiles"])]
    if not cands:
        if must_c3:
            c3 = next((t for t in hand if t["num"] == 3 and t["color"] == "cloud"), None)
            if c3: return _single_play(c3, maxnum, strset)
        if hand:
            return _single_play(_lowest_tile(hand, maxnum), maxnum, strset)
        return None

    sortkey = lambda c: (-len(c["tiles"]), tuple(c["combo"]["key"]))
    keyonly = lambda c: tuple(c["combo"]["key"])

    if p["smart_lead"] == "weak":
        if random.random() < p["lead_rand"]:
            return random.choice(cands)              # 아무 조합이나 (비효율)
        singles = sorted([c for c in cands if len(c["tiles"]) == 1], key=keyonly)
        return singles[0] if singles else sorted(cands, key=keyonly)[0]

    if p["smart_lead"] == "hard":
        if len(hand) <= 6 or must_c3:
            cands.sort(key=sortkey); return cands[0]
        # 평소엔 메이드(5장)·최고패(1,2)는 아껴 무기로 남기고 낮은 묶음부터 턴다
        base = [c for c in cands if len(c["tiles"]) < 5] or cands
        safe = [c for c in base if not _has_top(c["tiles"])] or base
        safe.sort(key=sortkey)
        if len(safe[0]["tiles"]) >= 2:
            return safe[0]
        cnt = _hand_counts(hand)
        lones = [t for t in hand if cnt[t["num"]] == 1] or hand
        return _single_play(_lowest_tile(lones, maxnum), maxnum, strset)

    # normal
    near = len(hand) <= 6
    if not near:
        cands.sort(key=sortkey)
    else:
        cands.sort(key=keyonly)
    for c in cands:
        if c["combo"]["count"] == 1 and not near and c["tiles"][0]["num"] in (1, 2):
            continue
        return c
    return cands[0]

def ai_follow(grade, hand, cur, maxnum, strset):
    p = ai_params(grade)
    plays = [x for x in enumerate_plays(hand, cur["count"], maxnum, strset) if beats(x["combo"], cur)]
    if not plays:
        return None
    plays.sort(key=lambda x: x["combo"]["key"])
    # 이번에 전부 털고 나갈 수 있으면 (중급 이상)
    if p["go_out"]:
        outs = [x for x in plays if len(x["tiles"]) == len(hand)]
        if outs:
            return min(outs, key=lambda x: x["combo"]["key"])
    # 약한 등급: 괜히 패스 / 아무거나 내기
    if p["pass_rand"] > 0 and random.random() < p["pass_rand"]:
        return None
    if p["random_play"]:
        return random.choice(plays)
    best = plays[0]
    if p["save_top"]:
        near = len(hand) <= cur["count"] + 3
        top = max(best["tiles"], key=lambda t: nrank(t["num"], maxnum))
        if not near:
            if p["save_top"] == "hard":
                if top["num"] == 2:
                    return None
                if top["num"] == 1 and random.random() < 0.6:
                    return None
            else:  # soft
                if top["num"] in (1, 2) and random.random() < 0.6:
                    return None
    return best

# ================= 방/게임 상태 =================
class Player:
    def __init__(self, name, is_ai=False):
        self.name = name
        self.is_ai = is_ai
        self.online = True
        self.ws = None
        self.hand = []
        self.chips = 0
        self.last = ""

class Room:
    _seq = 1000
    def __init__(self, name, max_players, host):
        Room._seq += 1
        self.id = str(Room._seq)
        self.name = name or ("방 " + self.id)
        self.max_players = max_players  # 2~5
        self.host = host                # username
        self.ai_grade = 3               # 1(약) ~ 5(강)
        self.players = []               # Player
        self.phase = "lobby"            # lobby/playing/roundend
        self.maxnum = PLAYER_MAX[max_players]
        self.strset = allowed_straights(self.maxnum)
        self.turn = 0
        self.leader = 0
        self.cur = None                 # {"tiles":[...], "cat":..., "by":seat}
        self.first_move = False
        self.log = []
        self.last_round = None
        self.ai_task = None
        self.del_task = None

    def seat_of(self, username):
        for i, p in enumerate(self.players):
            if not p.is_ai and p.name == username:
                return i
        return -1

    def active_count(self):
        return len(self.players)

ROOMS = {}       # id -> Room
TOKENS = {}      # token -> username
USER_WS = {}     # username -> ws (current)
USER_ROOM = {}   # username -> room id

def log_room(room, html):
    room.log.append(html)
    if len(room.log) > 60:
        room.log = room.log[-60:]

# ================= 메시지 전송 =================
async def send(ws, obj):
    if ws is None: return
    try:
        await ws.send_json(obj)
    except Exception:
        pass

def public_tiles(tiles):
    return [{"num": t["num"], "color": t["color"], "id": t["id"]} for t in tiles]

def room_public(room):
    return {
        "id": room.id, "name": room.name, "maxPlayers": room.max_players,
        "maxnum": room.maxnum, "phase": room.phase, "host": room.host, "aiGrade": room.ai_grade,
        "turn": room.turn, "leader": room.leader, "firstMove": room.first_move,
        "cur": ({"tiles": public_tiles(room.cur["tiles"]), "cat": room.cur["cat"],
                 "catKr": COMBO_KR[room.cur["cat"]], "by": room.cur["by"]} if room.cur else None),
        "players": [{
            "name": p.name, "isAI": p.is_ai, "online": p.online,
            "handCount": len(p.hand), "chips": p.chips, "last": p.last,
            "seat": i
        } for i, p in enumerate(room.players)],
        "log": room.log[-30:],
        "lastRound": room.last_round,
    }

async def broadcast_room(room):
    pub = room_public(room)
    for i, p in enumerate(room.players):
        if p.is_ai or not p.online or p.ws is None:
            continue
        msg = {"t": "room", "room": pub, "yourSeat": i,
               "yourHand": public_tiles(p.hand) if room.phase != "lobby" else []}
        await send(p.ws, msg)

async def send_lobby(ws):
    rooms = [{
        "id": r.id, "name": r.name, "maxPlayers": r.max_players,
        "count": r.active_count(), "phase": r.phase,
        "humans": sum(1 for p in r.players if not p.is_ai),
    } for r in ROOMS.values()]
    await send(ws, {"t": "lobby", "rooms": rooms})

async def broadcast_lobby():
    for uname, ws in list(USER_WS.items()):
        if uname not in USER_ROOM:
            await send_lobby(ws)

# ================= 게임 진행 =================
def start_round(room):
    deck = build_deck(room.maxnum)
    random.shuffle(deck)
    for p in room.players:
        p.hand = []
        p.last = ""
    per = len(deck) // len(room.players)
    for i, t in enumerate(deck):
        room.players[i % len(room.players)].hand.append(t)
    for p in room.players:
        sort_hand(p.hand, room.maxnum)
    # 구름3 보유자 = 선
    room.leader = 0
    for i, p in enumerate(room.players):
        if any(t["num"] == 3 and t["color"] == "cloud" for t in p.hand):
            room.leader = i; break
    room.turn = room.leader
    room.cur = None
    room.first_move = False
    room.phase = "playing"
    room.last_round = None
    log_room(room, f'<b>새 라운드</b> — {room.players[room.leader].name} 선 (구름3 보유)')

def do_play(room, seat, tiles):
    """검증 후 적용. 성공시 True"""
    p = room.players[seat]
    ids = set(t["id"] for t in tiles)
    if len(ids) != len(tiles):
        return False, "중복 타일"
    hand_ids = set(t["id"] for t in p.hand)
    if not ids.issubset(hand_ids):
        return False, "손패에 없는 타일"
    combo = classify(tiles, room.maxnum, room.strset)
    if not combo:
        return False, "유효하지 않은 조합"
    if room.cur is not None:
        if not beats(combo, {"count": room.cur["combo"]["count"], "key": room.cur["combo"]["key"]}):
            return False, "더 높은 패여야 합니다"
    # 적용
    p.hand = [t for t in p.hand if t["id"] not in ids]
    room.cur = {"tiles": tiles, "cat": combo["cat"], "combo": combo, "by": seat}
    room.leader = seat
    room.first_move = True
    p.last = COMBO_KR[combo["cat"]]
    log_room(room, f'<b>{p.name}</b> ▶ {COMBO_KR[combo["cat"]]} ({" ".join(CNAME[t["color"]]+str(t["num"]) for t in tiles)})')
    return True, None

def do_pass(room, seat):
    if room.cur is None:
        return False, "선은 패스할 수 없습니다"
    room.players[seat].last = "패스"
    log_room(room, f'{room.players[seat].name} … 패스')
    return True, None

def advance_turn(room):
    n = len(room.players)
    room.turn = (room.turn + 1) % n
    if room.cur is not None and room.turn == room.leader:
        room.cur = None
        log_room(room, f'— 모두 패스, <b>{room.players[room.leader].name}</b> 새로 시작')

def end_round(room, winner):
    rows = []
    for i, p in enumerate(room.players):
        rem = len(p.hand)
        twos = sum(1 for t in p.hand if t["num"] == 2)
        eff = rem * (2 ** twos)
        rows.append({"seat": i, "name": p.name, "rem": rem, "twos": twos, "eff": eff})
    s = sum(r["eff"] for r in rows)
    nn = len(rows)
    for r in rows:
        r["score"] = s - nn * r["eff"]
        room.players[r["seat"]].chips += r["score"]
    rows_sorted = sorted(rows, key=lambda r: r["eff"])
    room.phase = "roundend"
    room.last_round = {
        "winner": winner, "winnerName": room.players[winner].name,
        "rows": [{"name": r["name"], "seat": r["seat"], "rem": r["rem"], "twos": r["twos"],
                  "eff": r["eff"], "score": r["score"], "chips": room.players[r["seat"]].chips}
                 for r in rows_sorted],
    }
    log_room(room, f'🎉 <b>{room.players[winner].name}</b> 승리! 라운드 종료')
    # 누적 통계(승/판수) + 게임내역 영구 기록
    wp = room.players[winner]
    if not wp.is_ai and wp.name in USERS:
        USERS[wp.name]["wins"] = USERS[wp.name].get("wins", 0) + 1
    for p in room.players:
        if not p.is_ai and p.name in USERS:
            USERS[p.name]["games"] = USERS[p.name].get("games", 0) + 1
            asyncio.create_task(persist_user(p.name))
    detail = [{"name": r["name"], "score": r["score"], "rem": r["rem"],
               "eff": r["eff"], "twos": r["twos"], "isWinner": r["seat"] == winner,
               "isAI": room.players[r["seat"]].is_ai}
              for r in rows_sorted]
    asyncio.create_task(record_game(room.name, room.players[winner].name, detail))

async def run_turns(room):
    """AI 또는 오프라인 유저의 턴을 자동 진행. 인간 온라인 턴을 만나면 멈춤."""
    try:
        while room.phase == "playing":
            p = room.players[room.turn]
            auto = p.is_ai or (not p.online)
            if not auto:
                break
            grade = room.ai_grade if p.is_ai else 3
            await asyncio.sleep(max(0.5, 1.05 - grade * 0.08))  # 1등급 느긋 ~ 5등급 빠릿
            if room.phase != "playing":
                break
            if room.cur is None:
                choice = ai_lead(grade, p.hand, room.first_move, room.maxnum, room.strset)
                if choice:
                    do_play(room, room.turn, choice["tiles"])
                else:
                    # 선인데 낼게 없을 수 없음(최소 싱글). 안전장치.
                    if p.hand:
                        do_play(room, room.turn, [p.hand[0]])
            else:
                choice = ai_follow(grade, p.hand, room.cur["combo"], room.maxnum, room.strset)
                if choice:
                    do_play(room, room.turn, choice["tiles"])
                else:
                    do_pass(room, room.turn)
            if len(room.players[room.turn].hand) == 0:
                end_round(room, room.turn)
                await broadcast_room(room)
                return
            advance_turn(room)
            await broadcast_room(room)
        await broadcast_room(room)
    except Exception as e:
        print("run_turns error:", e)

def schedule_ai(room):
    if room.ai_task and not room.ai_task.done():
        return
    room.ai_task = asyncio.create_task(run_turns(room))

def schedule_room_delete(room, delay=90):
    """진행중 방에 온라인 사람이 아무도 없으면 유예 후 삭제(그 전 재접속하면 취소)."""
    if room.del_task and not room.del_task.done():
        return
    async def _del():
        try:
            await asyncio.sleep(delay)
            if room.id in ROOMS and not any((not p.is_ai and p.online) for p in room.players):
                ROOMS.pop(room.id, None)
                await broadcast_lobby()
        except asyncio.CancelledError:
            pass
    room.del_task = asyncio.create_task(_del())

def cancel_room_delete(room):
    if room.del_task and not room.del_task.done():
        room.del_task.cancel()
    room.del_task = None

# ================= 방 떠나기 정리 =================
async def leave_room(username):
    rid = USER_ROOM.get(username)
    if not rid or rid not in ROOMS:
        USER_ROOM.pop(username, None)
        return
    room = ROOMS[rid]
    seat = room.seat_of(username)
    USER_ROOM.pop(username, None)
    if room.phase == "lobby":
        if seat >= 0:
            room.players.pop(seat)
        # 호스트가 나가면 다른 인간에게 양도 or 방 삭제
        humans = [p for p in room.players if not p.is_ai]
        if not humans:
            ROOMS.pop(rid, None)
        else:
            if room.host == username:
                room.host = humans[0].name
            await broadcast_room(room)
    else:
        # 게임 중엔 오프라인 처리(자리 유지, AI가 대행)
        if seat >= 0:
            room.players[seat].online = False
            room.players[seat].ws = None
            log_room(room, f'{username} 접속 끊김 — 자동 대행')
            await broadcast_room(room)
            schedule_ai(room)
        humans_online = [p for p in room.players if not p.is_ai and p.online]
        if not humans_online:
            ROOMS.pop(rid, None)
    await broadcast_lobby()

# ================= WebSocket 핸들러 =================
async def handle_msg(ws, data):
    t = data.get("t")

    if t == "getver":
        return await send(ws, {"t": "ver", "v": current_ver()})

    if t == "ping":
        return await send(ws, {"t": "pong"})

    # ---- 인증 ----
    if t == "register":
        uid = (data.get("id") or "").strip()
        pw = data.get("pw") or ""
        if uid == ADMIN_ID:
            return await send(ws, {"t": "err", "msg": "사용할 수 없는 아이디입니다"})
        if len(uid) < 2 or len(uid) > 16:
            return await send(ws, {"t": "err", "msg": "아이디는 2~16자"})
        if len(pw) < 4:
            return await send(ws, {"t": "err", "msg": "비밀번호는 4자 이상"})
        if uid in USERS:
            return await send(ws, {"t": "err", "msg": "이미 있는 아이디입니다"})
        salt, h = hash_pw(pw)
        USERS[uid] = {"salt": salt, "hash": h, "wins": 0, "games": 0,
                      "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        await persist_user(uid)
        return await send(ws, {"t": "ok", "msg": "가입 완료! 로그인하세요"})

    if t == "login":
        uid = (data.get("id") or "").strip()
        pw = data.get("pw") or ""
        # 관리자 로그인 (로그인 화면에서 admin / 1234)
        if uid == ADMIN_ID:
            if pw != ADMIN_PW:
                return await send(ws, {"t": "err", "msg": "아이디 또는 비밀번호 오류"})
            token = secrets.token_hex(16)
            TOKENS[token] = ADMIN_ID
            ws["user"] = ADMIN_ID
            ws["is_admin"] = True
            await send(ws, {"t": "auth", "token": token, "id": ADMIN_ID, "admin": True, "wins": 0})
            return await send(ws, {"t": "adminData", **(await admin_data())})
        u = USERS.get(uid)
        if not u or not verify_pw(pw, u["salt"], u["hash"]):
            return await send(ws, {"t": "err", "msg": "아이디 또는 비밀번호 오류"})
        token = secrets.token_hex(16)
        TOKENS[token] = uid
        # 기존 접속 교체
        old = USER_WS.get(uid)
        if old and old is not ws:
            await send(old, {"t": "kick", "msg": "다른 곳에서 로그인됨"})
        USER_WS[uid] = ws
        ws["user"] = uid
        await send(ws, {"t": "auth", "token": token, "id": uid, "wins": u.get("wins", 0)})
        # 진행중 방 복귀?
        rid = USER_ROOM.get(uid)
        if rid and rid in ROOMS:
            room = ROOMS[rid]
            seat = room.seat_of(uid)
            if seat >= 0:
                room.players[seat].online = True
                room.players[seat].ws = ws
                cancel_room_delete(room)
                log_room(room, f'{uid} 재접속')
                await broadcast_room(room)
                return
        return await send_lobby(ws)

    if t == "resume":
        token = data.get("token") or ""
        uid = TOKENS.get(token)
        if uid == ADMIN_ID:
            ws["user"] = ADMIN_ID
            ws["is_admin"] = True
            await send(ws, {"t": "auth", "token": token, "id": ADMIN_ID, "admin": True, "wins": 0})
            return await send(ws, {"t": "adminData", **(await admin_data())})
        if not uid or uid not in USERS:
            return await send(ws, {"t": "needLogin"})
        old = USER_WS.get(uid)
        if old and old is not ws:
            await send(old, {"t": "kick", "msg": "다른 곳에서 로그인됨"})
        USER_WS[uid] = ws
        ws["user"] = uid
        await send(ws, {"t": "auth", "token": token, "id": uid, "wins": USERS[uid].get("wins", 0)})
        rid = USER_ROOM.get(uid)
        if rid and rid in ROOMS:
            room = ROOMS[rid]
            seat = room.seat_of(uid)
            if seat >= 0:
                room.players[seat].online = True
                room.players[seat].ws = ws
                cancel_room_delete(room)
                await broadcast_room(room)
                return
        return await send_lobby(ws)

    # 이하 로그인 필요
    uid = ws.get("user")
    if not uid:
        return await send(ws, {"t": "needLogin"})

    if t == "adminData":
        if not ws.get("is_admin"):
            return await send(ws, {"t": "err", "msg": "관리자 전용"})
        return await send(ws, {"t": "adminData", **(await admin_data())})

    if t == "sync":   # 앱 복귀 등: 최신 상태 즉시 재전송 + 온라인 복구
        if ws.get("is_admin"):
            return await send(ws, {"t": "adminData", **(await admin_data())})
        rid = USER_ROOM.get(uid)
        room = ROOMS.get(rid) if rid else None
        if room:
            seat = room.seat_of(uid)
            if seat >= 0 and not room.players[seat].online:
                room.players[seat].online = True
                room.players[seat].ws = ws
                cancel_room_delete(room)
            await broadcast_room(room)
        else:
            await send_lobby(ws)
        return

    if t == "lobby":
        return await send_lobby(ws)

    if t == "create":
        if uid in USER_ROOM:
            await leave_room(uid)
        name = (data.get("name") or "").strip()[:20]
        mp = int(data.get("maxPlayers") or 4)
        if mp not in PLAYER_MAX:
            return await send(ws, {"t": "err", "msg": "인원은 2~5"})
        room = Room(name, mp, uid)
        try:
            g = int(data.get("aiGrade"))
            if 1 <= g <= 5:
                room.ai_grade = g
        except (TypeError, ValueError):
            pass
        pl = Player(uid); pl.ws = ws
        room.players.append(pl)
        ROOMS[room.id] = room
        USER_ROOM[uid] = room.id
        await broadcast_room(room)
        await broadcast_lobby()
        return

    if t == "join":
        rid = data.get("roomId")
        room = ROOMS.get(rid)
        if not room:
            return await send(ws, {"t": "err", "msg": "없는 방"})
        if room.seat_of(uid) >= 0:
            await broadcast_room(room); return
        if room.phase != "lobby":
            return await send(ws, {"t": "err", "msg": "이미 시작된 방"})
        if room.active_count() >= room.max_players:
            return await send(ws, {"t": "err", "msg": "방이 가득 참"})
        if uid in USER_ROOM:
            await leave_room(uid)
        pl = Player(uid); pl.ws = ws
        room.players.append(pl)
        USER_ROOM[uid] = room.id
        log_room(room, f'{uid} 입장')
        await broadcast_room(room)
        await broadcast_lobby()
        return

    if t == "leave":
        await leave_room(uid)
        return await send_lobby(ws)

    # 이하 방 안에서만
    rid = USER_ROOM.get(uid)
    room = ROOMS.get(rid) if rid else None
    if not room:
        return await send(ws, {"t": "err", "msg": "방에 있지 않음"})
    seat = room.seat_of(uid)

    if t == "addAI":
        if room.host != uid or room.phase != "lobby":
            return
        if room.active_count() >= room.max_players:
            return await send(ws, {"t": "err", "msg": "자리 없음"})
        used = set(p.name for p in room.players)
        pool = [x for x in AI_NAMES if x not in used]
        nm = random.choice(pool) if pool else ("AI" + str(random.randint(1, 999)))
        room.players.append(Player(nm, is_ai=True))
        await broadcast_room(room); await broadcast_lobby()
        return

    if t == "setAiGrade":
        if room.host != uid or room.phase != "lobby":
            return
        try:
            g = int(data.get("grade"))
        except (TypeError, ValueError):
            return
        if 1 <= g <= 5:
            room.ai_grade = g
            await broadcast_room(room)
        return

    if t == "removeAI":
        if room.host != uid or room.phase != "lobby":
            return
        s = int(data.get("seat", -1))
        if 0 <= s < len(room.players) and room.players[s].is_ai:
            room.players.pop(s)
            await broadcast_room(room); await broadcast_lobby()
        return

    if t == "start":
        if room.host != uid or room.phase == "playing":
            return
        if room.active_count() != room.max_players:
            return await send(ws, {"t": "err", "msg": f"{room.max_players}명을 채워야 시작 (AI 추가 가능)"})
        start_round(room)
        await broadcast_room(room)
        schedule_ai(room)
        return

    if t == "play":
        if room.phase != "playing" or room.turn != seat:
            return await send(ws, {"t": "err", "msg": "당신 차례가 아닙니다"})
        ids = data.get("tileIds") or []
        tiles = [t for t in room.players[seat].hand if t["id"] in ids]
        okp, err = do_play(room, seat, tiles)
        if not okp:
            return await send(ws, {"t": "err", "msg": err})
        if len(room.players[seat].hand) == 0:
            end_round(room, seat)
            await broadcast_room(room)
            return
        advance_turn(room)
        await broadcast_room(room)
        schedule_ai(room)
        return

    if t == "pass":
        if room.phase != "playing" or room.turn != seat:
            return await send(ws, {"t": "err", "msg": "당신 차례가 아닙니다"})
        okp, err = do_pass(room, seat)
        if not okp:
            return await send(ws, {"t": "err", "msg": err})
        advance_turn(room)
        await broadcast_room(room)
        schedule_ai(room)
        return

    if t == "next":   # 다음 라운드 (호스트)
        if room.host != uid or room.phase != "roundend":
            return
        start_round(room)
        await broadcast_room(room)
        schedule_ai(room)
        return

    if t == "backToLobby":
        if room.host != uid:
            return
        room.phase = "lobby"
        room.cur = None
        room.last_round = None
        for p in room.players:
            p.hand = []; p.last = ""
        # 오프라인 유저/ 떠난 자리 정리
        await broadcast_room(room)
        return

    if t == "chat":
        msg = (data.get("msg") or "")[:120]
        if msg:
            log_room(room, f'💬 <b>{uid}</b>: {msg}')
            await broadcast_room(room)
        return

    if t == "voice":
        # 푸시투토크 음성(WAV base64)을 같은 방의 다른 온라인 유저에게 중계
        vdata = data.get("data")
        if not isinstance(vdata, str) or len(vdata) > 900000:  # 과대 메시지 차단
            return
        payload = {"t": "voice", "from": uid, "seat": seat, "data": vdata}
        for i, p in enumerate(room.players):
            if p.is_ai or not p.online or p.ws is None or i == seat:
                continue
            await send(p.ws, payload)
        return


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    ws["user"] = None
    await send(ws, {"t": "ver", "v": current_ver()})  # 버전 알림(자동 업데이트)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                await handle_msg(ws, data)
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        uid = ws.get("user")
        if uid:
            if USER_WS.get(uid) is ws:
                USER_WS.pop(uid, None)
            rid = USER_ROOM.get(uid)
            if rid and rid in ROOMS:
                room = ROOMS[rid]
                seat = room.seat_of(uid)
                if room.phase == "lobby":
                    await leave_room(uid)
                elif seat >= 0:
                    room.players[seat].online = False
                    room.players[seat].ws = None
                    log_room(room, f'{uid} 접속 끊김 — 자동 대행')
                    await broadcast_room(room)
                    schedule_ai(room)
                    if not any((not p.is_ai and p.online) for p in room.players):
                        schedule_room_delete(room)   # 바로 삭제 않고 유예(재접속 대기)
            await broadcast_lobby()
    return ws


INDEX_PATH = os.path.join(STATIC, "index.html")

def current_ver():
    """index.html 수정시각 기반 버전(배포 때마다 바뀜)."""
    try:
        return str(int(os.path.getmtime(INDEX_PATH)))
    except Exception:
        return "0"

async def index(request):
    # 캐시 금지 + 버전 주입 → 홈화면 앱도 새 버전 자동 반영
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            html = f.read().replace("%%BUILDVER%%", current_ver())
        resp = web.Response(text=html, content_type="text/html", charset="utf-8")
    except Exception:
        resp = web.FileResponse(INDEX_PATH)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

async def _on_startup(app):
    await db_init()

def make_app():
    app = web.Application()
    app.on_startup.append(_on_startup)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", STATIC)
    return app

if __name__ == "__main__":
    app = make_app()
    print("=" * 48)
    print(" 렉시오 온라인 서버 시작")
    print(f"  로컬:   http://localhost:{PORT}")
    print(f"  같은 와이파이: http://<이 PC의 IP>:{PORT}")
    print("=" * 48)
    web.run_app(app, host="0.0.0.0", port=PORT)
