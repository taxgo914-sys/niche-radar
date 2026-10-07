#!/usr/bin/env python3
"""
niche_finder.py
各国の検索エンジンのサジェスト(オートコンプリート)を深掘りして、
「多くの人ではなく、少数が検索しているニッチなワード」を毎日拾い上げる。

仕組み:
  1. シード語(ニッチ分野)ごとに「シード + あ/い/う… a/b/c…」「シード なぜ」等で展開
  2. Google / Bing / DuckDuckGo / Yahoo / Yahoo! JAPAN / Yandex / Naver / Baidu のサジェストを取得
  3. SQLite に記録し、「今日はじめて現れた語」だけを抽出
  4. ニッチ度スコアを付けて reports/YYYY-MM-DD.csv と .md に出力

ニッチ度スコア(0〜1, 高いほどニッチ):
  - サジェスト内の順位が下の方 (=検索量が少ない傾向)      40%
  - 語が長い (=ロングテール)                              30%
  - 1つのエンジンにしか出ない (=まだ広まっていない)        30%
  ※シード単体で出る「定番語」は除外

外部ライブラリ不要 (Python 3.9+ 標準ライブラリのみ)
"""
import argparse
import csv
import datetime as dt
import json
import random
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ---- 展開用の文字 ---------------------------------------------------------
EXPANDERS = {
    "latin": list("abcdefghijklmnopqrstuvwxyz0123456789"),
    "ja": list("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわ"),
    "ko": list("가나다라마바사아자차카타파하"),
    "ru": list("абвгдежзиклмнопрстуфхцчшэюя"),
    "de": list("abcdefghijklmnopqrstuvwxyzäöü"),
    "zh": ["怎么", "为什么", "哪里", "多少钱", "推荐", "教程", "是什么", "能不能"],
}

# ---- 疑問・意図パターン ({} にシードが入る) -------------------------------
QUESTIONS = {
    "ja": ["{} なぜ", "{} やり方", "{} 代わり", "{} 自作", "{} できない", "{} 初心者", "{} 違い"],
    "en": ["how {}", "why {}", "{} vs", "{} without", "can {}", "{} for", "{} alternative"],
    "ko": ["{} 방법", "{} 왜", "{} 추천", "{} 차이"],
    "ru": ["как {}", "почему {}", "{} своими руками", "{} или"],
    "de": ["wie {}", "warum {}", "{} selber machen", "{} oder"],
    "zh": [],
}


# ---- HTTP ------------------------------------------------------------------
def http_get(url, timeout=10, ua=None):
    req = urllib.request.Request(url, headers={"User-Agent": ua or UA, "Accept-Language": "*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        charset = r.headers.get_content_charset()
    for enc in (charset, "utf-8", "gb18030", "cp932"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", "replace")


def q(params):
    return urllib.parse.urlencode(params)


def _opensearch(text):
    data = json.loads(text)
    return [s for s in data[1] if isinstance(s, str)]


# ---- 各エンジン --------------------------------------------------------------
def google(term, m):
    return _opensearch(http_get("https://suggestqueries.google.com/complete/search?" + q({
        "client": "firefox", "ie": "utf-8", "oe": "utf-8",
        "hl": m.get("hl", "en"), "gl": m.get("gl", "us"), "q": term})))


def bing(term, m):
    return _opensearch(http_get("https://api.bing.com/osjson.aspx?" + q({
        "query": term, "mkt": m.get("mkt", "en-US")})))


def ddg(term, m):
    return _opensearch(http_get("https://duckduckgo.com/ac/?" + q({
        "q": term, "kl": m.get("kl", "wt-wt"), "type": "list"})))


def yandex(term, m):
    return _opensearch(http_get("https://suggest.yandex.ru/suggest-ff.cgi?" + q({
        "part": term, "uil": m.get("hl", "ru")})))


def baidu(term, m):
    return _opensearch(http_get("https://suggestion.baidu.com/su?" + q({
        "wd": term, "action": "opensearch", "ie": "utf-8"})))


def naver(term, m):
    d = json.loads(http_get("https://ac.search.naver.com/nx/ac?" + q({
        "q": term, "con": "0", "frm": "nv", "ans": "2", "r_format": "json",
        "r_enc": "UTF-8", "r_unicode": "0", "t_koreng": "1", "st": "100"})))
    out = []
    for it in (d.get("items") or [[]])[0]:
        w = it[0] if isinstance(it, list) and it else it
        if isinstance(w, list) and w:
            w = w[0]
        if isinstance(w, str):
            out.append(w)
    return out


def _pick_strings(obj, keys=("k", "key", "Suggest", "phrase", "word")):
    """Yahoo 系は返り値の形がいくつかあるので、候補キーの文字列を全部拾う"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str):
                out.append(v)
            else:
                out += _pick_strings(v, keys)
    elif isinstance(obj, list):
        for v in obj:
            out += _pick_strings(v, keys)
    return out


def _try_urls(urls):
    last = None
    for url in urls:
        try:
            data = json.loads(http_get(url))
            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                sugs = [s for s in data[1] if isinstance(s, str)]
            else:
                sugs = _pick_strings(data)
            if sugs:
                return list(dict.fromkeys(sugs))
        except Exception as e:  # noqa: BLE001
            last = e
    if last:
        raise last
    return []


def yahoo_jp(term, m):
    """Yahoo! JAPAN 独自のサジェスト (日本の Yahoo 利用者の検索に基づく)"""
    return _try_urls([
        "https://assist.search.yahooapis.jp/SuggestSearchService/V5/webassistSearch?"
        + q({"output": "json", "p": term}),
        "https://search.yahoo.co.jp/api/v1/suggest?" + q({"p": term}),
    ])


def yahoo(term, m):
    """Yahoo (米国ほか)。検索結果は Bing だが、サジェストは Yahoo 独自"""
    region = m.get("yahoo_region", "us")
    return _try_urls([
        f"https://search.yahoo.com/sugg/gossip/gossip-{region}-ura/?"
        + q({"output": "sd1", "command": term, "nresults": 10}),
        "https://ff.search.yahoo.com/gossip?" + q({"output": "json", "command": term}),
    ])


ENGINES = {"google": google, "bing": bing, "ddg": ddg,
           "yandex": yandex, "baidu": baidu, "naver": naver,
           "yahoo_jp": yahoo_jp, "yahoo": yahoo}


def demo_fetch(engine, term, m):
    """ネットに出ずに動作確認するための疑似サジェスト"""
    rnd = random.Random(f"{engine}|{term}|{dt.date.today()}")
    return [f"{term} " + "".join(rnd.choice("xyzwq") for _ in range(rnd.randint(2, 7)))
            for _ in range(rnd.randint(3, 8))]


# ---- DB ---------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS terms(
  market TEXT, term TEXT, first_seen TEXT, last_seen TEXT, days_seen INTEGER,
  PRIMARY KEY(market, term));
CREATE TABLE IF NOT EXISTS observations(
  date TEXT, market TEXT, engine TEXT, seed TEXT, prefix TEXT,
  term TEXT, rank INTEGER, n INTEGER, is_head INTEGER);
CREATE INDEX IF NOT EXISTS idx_obs ON observations(date, market, term);
CREATE TABLE IF NOT EXISTS translations(term TEXT PRIMARY KEY, ja TEXT);
CREATE TABLE IF NOT EXISTS seed_log(
  date TEXT, market TEXT, seed TEXT, source TEXT, cat TEXT,
  PRIMARY KEY(date, market, seed));
CREATE TABLE IF NOT EXISTS snowball(
  date TEXT, market TEXT, term TEXT, score REAL, cat TEXT, seed TEXT,
  PRIMARY KEY(date, market, term));
"""


def normalize(s):
    return " ".join(unicodedata.normalize("NFKC", s).lower().split())


def record(db, today, market, engine, seed, prefix, sugs, is_head):
    n = len(sugs)
    for rank, s in enumerate(sugs):
        term = normalize(s)
        if not term or term == normalize(seed):
            continue
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)",
                   (today, market, engine, seed, prefix, term, rank, n, int(is_head)))
        cur = db.execute("SELECT last_seen FROM terms WHERE market=? AND term=?", (market, term)).fetchone()
        if cur is None:
            db.execute("INSERT INTO terms VALUES(?,?,?,?,1)", (market, term, today, today))
        elif cur[0] != today:
            db.execute("UPDATE terms SET last_seen=?, days_seen=days_seen+1 WHERE market=? AND term=?",
                       (today, market, term))


def prefixes(seed, m):
    yield seed, True                                   # 定番語(除外用)
    for key in m.get("expand", []):
        for ch in EXPANDERS.get(key, []):
            yield f"{seed} {ch}", False
    for tpl in QUESTIONS.get(m.get("questions", ""), []):
        yield tpl.format(seed), False


# ---- 今日のシード選び ---------------------------------------------------------
# 1) 辞書(seeds.json): IT・科学・経済を厚めに、毎日違う語を回す
# 2) Wikipedia の新着記事: その日に生まれた話題 (IT/科学/経済の記事だけ)
# 3) 雪だるま: 直近の高スコア語を、次の日のシードにする
# 4) config.json の seeds: 固定で毎日回したい語 (空でよい)
WIKI_UA = "NicheRadar/1.0 (personal hobby project; search suggestion research)"

# 言語ごとの、記事カテゴリ名に含まれる語。IT/経済/科学のどれに当たるかで分野を決める
WIKI_RULES = {
    "ja": {
        "it": r"コンピュータ|ソフトウェア|プログラミング|情報技術|インターネット|ネットワーク|半導体|暗号|人工知能|データベース",
        "economy": r"経済|金融|経営|会計|投資|貿易|市場|通貨|税",
        "science": r"科学|物理|化学|生物|数学|天文|地学|医学|工学|技術|材料",
        "person": r"生年|没年|の人物|出身の|の人$|選手|俳優|声優|の政治家",
    },
    "en": {
        "it": r"computing|software|programming|internet|computer|network|semiconductor|cryptograph|artificial intelligence|database",
        "economy": r"econom|financ|business|accounting|invest|trade|market|currenc|tax",
        "science": r"science|physics|chemistry|biology|mathematic|astronom|geolog|medicine|engineering|technology|material",
        "person": r"births|deaths|living people|people from|alumni|players|politicians|actors",
    },
    "ko": {
        "it": r"컴퓨터|소프트웨어|프로그래밍|인터넷|네트워크|반도체|암호|인공지능|데이터베이스",
        "economy": r"경제|금융|경영|회계|투자|무역|시장|통화|세금",
        "science": r"과학|물리|화학|생물|수학|천문|지구|의학|공학|기술|재료",
        "person": r"출생|사망|인물|출신|선수|배우",
    },
    "ru": {
        "it": r"программн|компьютер|информац|интернет|сеть|полупроводник|криптограф|искусственный интеллект|баз данных",
        "economy": r"экономик|финанс|бизнес|бухгалтер|инвести|торговл|рынок|валют|налог",
        "science": r"наук|физик|хими|биолог|математ|астроном|геолог|медицин|техник|технолог|материал",
        "person": r"родил|умерл|персоналии|выпускник|спортсмен|актёр|актер",
    },
    "de": {
        "it": r"informatik|software|programmier|internet|netzwerk|halbleiter|kryptograf|künstliche intelligenz|datenbank",
        "economy": r"wirtschaft|finanz|betriebswirtschaft|buchhaltung|investition|handel|markt|währung|steuer",
        "science": r"wissenschaft|physik|chemie|biologie|mathematik|astronomie|geologie|medizin|technik|werkstoff",
        "person": r"geboren|gestorben|mitglied|sportler|schauspieler|politiker",
    },
    "zh": {
        "it": r"计算机|電腦|计算机|软件|軟件|軟體|编程|程式|互联网|網際網路|网络|半导体|半導體|密码|人工智能|数据库|資料庫",
        "economy": r"经济|經濟|金融|商业|商業|会计|會計|投资|投資|贸易|貿易|市场|市場|货币|貨幣|税",
        "science": r"科学|科學|物理|化学|化學|生物|数学|數學|天文|地质|地質|医学|醫學|工程|技术|技術|材料",
        "person": r"出生|逝世|人物|校友|球员|球員|演员|演員|政治人物",
    },
}
CATS = ("it", "science", "economy", "other")
LEAD_WORDS = {"how", "why", "can", "what", "does", "is", "do", "wie", "warum", "как", "почему"}


def _clean_title(title):
    return re.sub(r"\s*[（(].*?[）)]\s*$", "", title).strip()


def wiki_candidates(m, max_pages=6):
    """Wikipedia の新着記事から (タイトル, 分野) を返す。IT/経済/科学の記事だけ"""
    lang = m.get("wiki_lang")
    rules = WIKI_RULES.get(lang)
    if not rules:
        return []
    params = {"action": "query", "format": "json", "generator": "recentchanges",
              "grcnamespace": 0, "grctype": "new", "grclimit": 500, "grcshow": "!redirect",
              "prop": "categories", "cllimit": "max", "clshow": "!hidden"}
    pages, cont = {}, {}
    for _ in range(max_pages):
        data = json.loads(http_get(
            f"https://{lang}.wikipedia.org/w/api.php?" + q({**params, **cont}), timeout=20, ua=WIKI_UA))
        for pid, p in ((data.get("query") or {}).get("pages") or {}).items():
            cur = pages.setdefault(pid, {"title": p.get("title", ""), "cats": []})
            cur["cats"] += [c.get("title", "") for c in p.get("categories", [])]
        cont = data.get("continue") or {}
        if not cont:
            break
        time.sleep(0.5)
    out = []
    for p in pages.values():
        title = _clean_title(p["title"])
        cats = " ".join(p["cats"]).lower()
        if not (2 <= len(title) <= 30) or ":" in title or not cats:
            continue
        if re.search(rules["person"], cats, re.I):
            continue
        for cat in ("it", "economy", "science"):
            if re.search(rules[cat], cats, re.I):
                out.append((title, cat))
                break
    return out


def make_seed(term, parent=""):
    """見つかったワードから、次の日のシードを作る。親シードの「続き」の部分を使い、話題を一歩奥へ進める"""
    toks = term.split()
    while len(toks) > 1 and toks[0] in LEAD_WORDS:      # 「how」「why」などの疑問語は外す
        toks = toks[1:]
    ptoks = normalize(parent).split()
    if ptoks and toks[:len(ptoks)] == ptoks and len(toks) > len(ptoks):
        toks = [ptoks[-1]] + toks[len(ptoks):]          # 親の最後の語 + 続きの語
    toks = toks[:3]
    while len(toks) > 1 and len(" ".join(toks)) > 24:   # 単語の途中で切らない
        toks = toks[:-1]
    seed = " ".join(toks)[:24].strip()
    return "" if normalize(seed) == normalize(parent) else seed


def _weighted_sample(rnd, items, weights, k):
    items, weights, out = list(items), list(weights), []
    while items and len(out) < k:
        i = rnd.choices(range(len(items)), weights=weights)[0]
        out.append(items.pop(i))
        weights.pop(i)
    return out


def pick_seeds(cfg, m, db, today, wiki_fn=None):
    """この国の今日のシード [(seed, cat, source)] を決める。同じ日に再実行しても同じ結果"""
    name = m["name"]
    done = db.execute("SELECT seed, cat, source FROM seed_log WHERE date=? AND market=? ORDER BY rowid",
                      (today, name)).fetchall()
    if done:
        return done

    n = m.get("seeds_per_day", cfg.get("seeds_per_day", 10))
    rnd = random.Random(f"{name}|{today}")
    day = dt.date.fromisoformat(today)
    cool = (day - dt.timedelta(days=cfg.get("seed_cooldown_days", 20))).isoformat()
    recent = {r[0] for r in db.execute(
        "SELECT seed FROM seed_log WHERE market=? AND date>=?", (name, cool))}
    chosen, names = [], set()

    def add(seed, cat, source):
        key = normalize(seed)
        if not seed or key in names or len(chosen) >= n + len(m.get("seeds", [])):
            return False
        names.add(key)
        chosen.append((seed, cat, source))
        return True

    for s in m.get("seeds", []):                       # 4) 固定シード
        add(s, "other", "pinned")

    # 2) Wikipedia 新着
    want_wiki = cfg.get("wiki_seeds_per_market", 3)
    if want_wiki and wiki_fn and m.get("wiki_lang"):
        try:
            cands = [(t, c) for t, c in wiki_fn(m) if t not in recent]
            rnd.shuffle(cands)
            got = 0
            for t, c in cands:
                if got >= want_wiki:
                    break
                got += add(t, c, "wiki")
        except Exception as e:  # noqa: BLE001
            print(f"  ! Wikipedia ({m.get('wiki_lang')}): {e}", file=sys.stderr)

    # 3) 雪だるま: 直近3日の高スコア語
    want_snow = cfg.get("snowball_seeds_per_market", 2)
    if want_snow:
        since = (day - dt.timedelta(days=3)).isoformat()
        rows = db.execute("""SELECT term, score, cat, seed FROM snowball
                             WHERE market=? AND date>=? AND date<?""", (name, since, today)).fetchall()
        recent_n = {normalize(x) for x in recent}
        cand = {}
        for term, score, cat, parent in rows:
            seed = make_seed(term, parent)
            if len(seed) >= 3 and normalize(seed) not in recent_n and normalize(seed) not in names:
                if seed not in cand or cand[seed][0] < score:
                    cand[seed] = (score, cat)
        picked = _weighted_sample(rnd, list(cand), [max(cand[s][0], 0.05) for s in cand], want_snow)
        for seed in picked:
            add(seed, cand[seed][1], "snowball")

    # 1) 辞書で残りを埋める (分野の重みに従って)
    pool = (cfg.get("_pool") or {}).get(name, {})
    weights = cfg.get("category_weights", {"it": 3, "science": 3, "economy": 3, "other": 1})
    lastuse = dict(db.execute("SELECT seed, MAX(date) FROM seed_log WHERE market=? GROUP BY seed", (name,)))
    for relax in (False, True):                        # 足りなければ、いちばん使っていない語から再利用
        avail = {c: [s for s in pool.get(c, [])
                     if normalize(s) not in names and (relax or s not in recent)] for c in CATS}
        if relax:
            for c in CATS:
                rnd.shuffle(avail[c])
                avail[c].sort(key=lambda s: lastuse.get(s, ""), reverse=True)   # 末尾 = 最も古い
        while len(chosen) < n + len(m.get("seeds", [])):
            cats = [c for c in CATS if avail[c]]
            if not cats:
                break
            c = rnd.choices(cats, weights=[weights.get(x, 1) for x in cats])[0]
            s = avail[c].pop() if relax else avail[c].pop(rnd.randrange(len(avail[c])))
            add(s, c, "pool")
        if len(chosen) >= n + len(m.get("seeds", [])):
            break

    for seed, cat, source in chosen:
        db.execute("INSERT OR IGNORE INTO seed_log VALUES(?,?,?,?,?)", (today, name, seed, source, cat))
    db.commit()
    return chosen


# ---- 収集 ---------------------------------------------------------------------
def collect(cfg, db, today, fetch, demo=False, depth=1, only=None, wiki_fn=None):
    total_budget = cfg.get("max_requests_per_run", 3000)
    lo, hi = cfg.get("sleep_seconds", [1.0, 2.5])
    per_day = cfg.get("prefixes_per_seed_per_day")  # 毎日一部だけ回して数日で一巡
    errors = 0
    for m in cfg["markets"]:
        if only and m["name"] not in only:
            continue
        budget = min(total_budget, cfg.get("max_requests_per_market", total_budget))
        for seed, cat, source in pick_seeds(cfg, m, db, today, wiki_fn):
            print(f"[{m['name']}] {seed}  ({cat}/{source})", file=sys.stderr)
            jobs = list(prefixes(seed, m))
            if per_day and len(jobs) - 1 > per_day:
                rest = jobs[1:]
                random.Random(f"{seed}|{today}").shuffle(rest)
                jobs = [jobs[0]] + rest[:per_day]
            for prefix, is_head in jobs:
                for eng in m["engines"]:
                    if budget <= 0 or total_budget <= 0:
                        break
                    try:
                        sugs = fetch(eng, prefix, m)
                    except Exception as e:  # noqa: BLE001
                        errors += 1
                        print(f"  ! {eng} '{prefix}': {e}", file=sys.stderr)
                        sugs = []
                    budget -= 1
                    total_budget -= 1
                    record(db, today, m["name"], eng, seed, prefix, sugs, is_head)
                    if not demo:
                        time.sleep(random.uniform(lo, hi))
            # depth 2: 今日の新語をもう一段掘る
            if depth >= 2:
                rows = db.execute("""
                    SELECT DISTINCT o.term FROM observations o JOIN terms t
                      ON t.market=o.market AND t.term=o.term
                    WHERE o.date=? AND o.market=? AND o.seed=? AND t.first_seen=? AND o.is_head=0
                    LIMIT ?""", (today, m["name"], seed, today, cfg.get("depth2_per_seed", 20))).fetchall()
                eng = m["engines"][0]
                for (term,) in rows:
                    if budget <= 0:
                        break
                    try:
                        sugs = fetch(eng, term, m)
                    except Exception as e:  # noqa: BLE001
                        errors += 1
                        sugs = []
                    budget -= 1
                    total_budget -= 1
                    record(db, today, m["name"], eng, seed, "depth2:" + term, sugs, False)
                    if not demo:
                        time.sleep(random.uniform(lo, hi))
            db.commit()
    return errors


# ---- レポート -----------------------------------------------------------------
def report(cfg, db, today, out_dir, top=30):
    engines_total = {m["name"]: len(m["engines"]) for m in cfg["markets"]}
    rows = db.execute("""
        SELECT o.market, o.term, MIN(o.seed),
               COUNT(DISTINCT o.engine),
               MAX(CAST(o.rank AS REAL) / MAX(o.n - 1, 1)),
               GROUP_CONCAT(DISTINCT o.engine)
        FROM observations o JOIN terms t ON t.market=o.market AND t.term=o.term
        WHERE o.date=? AND t.first_seen=?
        GROUP BY o.market, o.term
        HAVING SUM(o.is_head)=0""", (today, today)).fetchall()

    cats = {(mk, sd): c for mk, sd, c in db.execute(
        "SELECT market, seed, cat FROM seed_log WHERE date=?", (today,))}
    results = []
    for market, term, seed, n_eng, depth, engines in rows:
        total = engines_total.get(market, 1)
        rarity = 1.0 - (n_eng - 1) / max(total - 1, 1)
        length = min(len(term) / 30, 1.0)
        score = round(0.4 * depth + 0.3 * length + 0.3 * rarity, 3)
        results.append(dict(date=today, market=market, seed=seed, term=term,
                            score=score, engines=engines, cat=cats.get((market, seed), "other")))
    results.sort(key=lambda r: (r["market"], -r["score"]))

    # 明日のシード候補 (雪だるま): 各国の上位だけ残す
    keep = cfg.get("snowball_keep_per_market", 30)
    seen = {}
    for r in results:
        if seen.get(r["market"], 0) < keep:
            seen[r["market"]] = seen.get(r["market"], 0) + 1
            db.execute("INSERT OR REPLACE INTO snowball VALUES(?,?,?,?,?,?)",
                       (today, r["market"], r["term"], r["score"], r["cat"], r["seed"]))
    db.commit()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{today}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date", "market", "cat", "seed", "term", "score", "engines"])
        w.writeheader()
        w.writerows(results)

    md = [f"# ニッチ検索ワード {today}", "",
          f"新たに見つかった語: {len(results)} 件", ""]
    for market in sorted({r["market"] for r in results}):
        md += [f"## {market}", "", "| スコア | ワード | シード | エンジン |", "|---|---|---|---|"]
        for r in [x for x in results if x["market"] == market][:top]:
            md.append(f"| {r['score']} | {r['term']} | {r['seed']} | {r['engines']} |")
        md.append("")
    md_path = out / f"{today}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    return csv_path, md_path, len(results), results


def export_json(results, today, data_dir, keep_days=60):
    """スマホアプリ用: data/YYYY-MM-DD.json と data/index.json を書き出す"""
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    items = [{k: r[k] for k in ("market", "cat", "seed", "term", "score", "engines", "ja") if k in r} for r in results]
    (d / f"{today}.json").write_text(
        json.dumps({"date": today, "items": items}, ensure_ascii=False), encoding="utf-8")
    days = sorted((p.stem for p in d.glob("????-??-??.json")), reverse=True)
    for old in days[keep_days:]:
        (d / f"{old}.json").unlink()
    days = days[:keep_days]
    index = []
    for day in days:
        data = json.loads((d / f"{day}.json").read_text(encoding="utf-8"))
        counts = {}
        for it in data["items"]:
            counts[it["market"]] = counts.get(it["market"], 0) + 1
        index.append({"date": day, "total": len(data["items"]), "markets": counts})
    (d / "index.json").write_text(json.dumps({"days": index}, ensure_ascii=False), encoding="utf-8")


# ---- 翻訳 (日本語) ------------------------------------------------------------
def _gtranslate(texts, target="ja"):
    """Google 翻訳の無料窓口で、改行区切りでまとめて訳す (非公式)"""
    url = "https://translate.googleapis.com/translate_a/single?" + q({
        "client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": "\n".join(texts)})
    data = json.loads(http_get(url, timeout=20))
    joined = "".join(seg[0] for seg in data[0] if seg and seg[0])
    lines = [x.strip() for x in joined.split("\n")]
    if len(lines) != len(texts):
        raise ValueError("行数がずれた")
    return lines


def translate_results(db, results, cfg, demo=False):
    """日本以外のワードに ja (日本語訳) を付ける。訳は DB に保存して使い回す"""
    if not cfg.get("translate", True):
        return
    skip = set(cfg.get("translate_skip_markets", ["JP"]))
    todo = sorted({r["term"] for r in results if r["market"] not in skip})
    cached = {}
    for i in range(0, len(todo), 500):
        chunk = todo[i:i + 500]
        cached.update(db.execute(
            f"SELECT term, ja FROM translations WHERE term IN ({','.join('?' * len(chunk))})", chunk).fetchall())
    need = [t for t in todo if t not in cached][:cfg.get("max_translations_per_run", 3000)]
    batch, size, errors = [], 0, 0

    def flush(b):
        nonlocal errors
        if not b:
            return
        try:
            out = [f"[訳] {t}" for t in b] if demo else _gtranslate(b)
        except Exception:  # noqa: BLE001  まとめて失敗したら1件ずつ
            out = []
            for t in b:
                try:
                    out.append(_gtranslate([t])[0])
                except Exception:  # noqa: BLE001
                    errors += 1
                    out.append(None)
                if not demo:
                    time.sleep(0.5)
        for t, ja in zip(b, out):
            if ja:
                cached[t] = ja
                db.execute("INSERT OR REPLACE INTO translations VALUES(?,?)", (t, ja))
        db.commit()
        if not demo:
            time.sleep(random.uniform(0.8, 1.6))

    for t in need:
        if len(batch) >= 40 or size + len(t) > 1500:
            flush(batch)
            batch, size = [], 0
        batch.append(t)
        size += len(t) + 1
    flush(batch)
    for r in results:
        if r["term"] in cached:
            r["ja"] = cached[r["term"]]
    print(f"翻訳: {len(need)} 件 (失敗 {errors})", file=sys.stderr)


def prune(db, keep_days=14):
    """DB を小さく保つ: 古い観測は消す (初出日の記録 terms は残す)"""
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    db.execute("DELETE FROM observations WHERE date < ?", (cutoff,))
    db.execute("DELETE FROM snowball WHERE date < ?", (cutoff,))
    old = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    db.execute("DELETE FROM seed_log WHERE date < ?", (old,))
    db.commit()
    db.execute("VACUUM")


def main():
    ap = argparse.ArgumentParser(description="各国検索エンジンのニッチワード収集")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--db", default="niche.db")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--depth", type=int, default=1, help="2 で新語をさらに一段深掘り")
    ap.add_argument("--markets", help="例: JP,US (省略時は全部)")
    ap.add_argument("--top", type=int, default=30, help="md に出す各国の上位件数")
    ap.add_argument("--demo", action="store_true", help="ネットに出ず疑似データで動作確認")
    ap.add_argument("--json-dir", help="スマホアプリ用 JSON の出力先 (例: docs/data)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    pool_path = cfg_path.parent / cfg.get("seed_pool_file", "seeds.json")
    cfg["_pool"] = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else {}
    if not cfg["_pool"]:
        print(f"! シード辞書 {pool_path} が見つかりません (config.json の seeds だけで動きます)", file=sys.stderr)
    db = sqlite3.connect(args.db)
    db.executescript(SCHEMA)
    today = dt.date.today().isoformat()
    fetch = demo_fetch if args.demo else (lambda e, t, m: ENGINES[e](t, m))
    only = set(args.markets.split(",")) if args.markets else None
    wiki_fn = None if args.demo else wiki_candidates

    errors = collect(cfg, db, today, fetch, demo=args.demo, depth=args.depth, only=only, wiki_fn=wiki_fn)
    csv_path, md_path, n, results = report(cfg, db, today, args.out, args.top)
    if args.json_dir:
        translate_results(db, results, cfg, demo=args.demo)
        export_json(results, today, args.json_dir, cfg.get("keep_days", 60))
    prune(db)
    print(f"完了: 新語 {n} 件 / エラー {errors} 件 → {csv_path}, {md_path}")


if __name__ == "__main__":
    main()
