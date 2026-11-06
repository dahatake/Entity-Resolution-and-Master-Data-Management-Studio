"""標準化(FR-STD-01〜03)。設定は版管理され、ハッシュで同一性を判定する。"""
import datetime
import re
import unicodedata

from .util import ApiError, jdump, sha

DEFAULT_CORP = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社", "(株)", "(有)", "(合)", "(同)",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人", "医療法人", "社会福祉法人", "学校法人", "特定非営利活動法人",
    "co.,ltd.", "co.,ltd", "co.ltd", "co., ltd.", "co., ltd", "corporation", "incorporated", "inc.", "inc", "corp.", "corp",
    "ltd.", "ltd", "llc", "k.k.",
]
# 異体字辞書の既定値。PoC-03 では「斎/齋」「国/國」を意図的に未登録とし、取りこぼしを確認した。
DEFAULT_VARIANTS = {"髙": "高", "﨑": "崎", "邉": "辺", "邊": "辺", "德": "徳", "廣": "広", "櫻": "桜", "濱": "浜", "澤": "沢"}

KANJI_NUM = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def kanji_to_int(s: str) -> int:
    if all(c in KANJI_NUM for c in s):
        return int("".join(str(KANJI_NUM[c]) for c in s))
    total, cur = 0, 0
    for c in s:
        if c == "百":
            total += (cur or 1) * 100; cur = 0
        elif c == "十":
            total += (cur or 1) * 10; cur = 0
        else:
            cur = KANJI_NUM[c]
    return total + cur


def op_nfkc(s, cfg): return unicodedata.normalize("NFKC", s)
def op_lower(s, cfg): return s.casefold()
def op_ws(s, cfg): return re.sub(r"\s+", " ", s).strip()
def op_nospace(s, cfg): return re.sub(r"\s+", "", s)


def op_punct(s, cfg):
    return "".join(ch for ch in s if not unicodedata.category(ch).startswith(("P", "S", "Z", "C")) or ch in "ー")


def op_kana(s, cfg):
    return "".join(chr(ord(c) + 0x60) if "\u3041" <= c <= "\u3096" else c for c in s)


def op_variant(s, cfg):
    m = cfg["variants"] if "variants" in cfg else DEFAULT_VARIANTS
    return "".join(m.get(c, c) for c in s)


def op_corp(s, cfg):
    forms = sorted(cfg.get("corp_forms") or DEFAULT_CORP, key=len, reverse=True)
    out = s
    for f in forms:
        f = unicodedata.normalize("NFKC", f).casefold()
        if re.fullmatch(r"[a-z0-9., ]+", f):
            out = re.sub(r"(?<![a-z0-9])" + re.escape(f) + r"(?![a-z0-9])", " ", out)
        else:
            out = out.replace(f, " ")
    return re.sub(r"\s+", " ", out).strip()


def op_digits(s, cfg): return re.sub(r"\D", "", s)


def op_phone(s, cfg):
    d = re.sub(r"\D", "", s)
    if d.startswith("81") and len(d) >= 11:
        d = "0" + d[2:]
    return d


def op_email(s, cfg): return s.strip().casefold()


def op_date(s, cfg):
    s = s.strip()
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$", s) or re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if not m:
        return ""
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def op_addr(s, cfg):
    """住所: 丁目・番地・号・ハイフンの表記を数字とハイフンへ揃え、建物名・階数を落とす。"""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[‐‑‒–—―−─－]+", "-", s)
    s = re.sub(r"(?<=\d)[ーｰ]+(?=\d)", "-", s)
    s = re.sub(r"([〇一二三四五六七八九十百]+)(?=丁目|番地|番|号|条|の|-)", lambda m: str(kanji_to_int(m.group(1))), s)
    s = re.sub(r"(\d+)丁目", r"\1-", s)
    s = re.sub(r"(\d+)番地?", r"\1-", s)
    s = re.sub(r"(\d+)号", r"\1", s)
    s = re.sub(r"(?<=\d)の(?=\d)", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    m = re.match(r"^(\D*?\d+(?:-\d+)*)", s)
    if m:
        s = m.group(1)
    return s.strip("-")


def op_addr_town(s, cfg):
    """正規化済みの住所から、番号部分を除いた町字までを返す。"""
    m = re.match(r"^(\D*)\d", s)
    return m.group(1) if m else s


def op_addr_num(s, cfg):
    m = re.search(r"(\d+(?:-\d+)*)$", s)
    return m.group(1) if m else ""


OPS = {"nfkc": op_nfkc, "lower": op_lower, "ws": op_ws, "nospace": op_nospace, "punct": op_punct, "kana": op_kana,
       "variant": op_variant, "corp": op_corp, "digits": op_digits, "phone": op_phone, "email": op_email, "date": op_date, "addr": op_addr, "addr_town": op_addr_town, "addr_num": op_addr_num}


def validate(cfg: dict):
    if not isinstance(cfg, dict) or not isinstance(cfg.get("fields"), list) or not cfg["fields"]:
        raise ApiError(400, "標準化設定には fields(1件以上)が必要です")
    seen = set()
    for f in cfg["fields"]:
        n = f.get("name")
        if not n or n in seen:
            raise ApiError(400, f"フィールド名が空または重複しています: {n}")
        seen.add(n)
        bad = [o for o in f.get("ops", []) if o not in OPS]
        if bad:
            raise ApiError(400, f"未知の操作: {bad}(利用可能: {sorted(OPS)})")
        if not isinstance(f.get("sources"), dict) or not f["sources"]:
            raise ApiError(400, f"{n}: sources(ソース名→列名の配列)が必要です")


def config_hash(cfg: dict) -> str:
    return sha(jdump(cfg))


def referenced_columns(cfg: dict):
    cols = set()
    for f in cfg["fields"]:
        for lst in f["sources"].values():
            cols.update(lst)
    return cols


def apply(cfg: dict, source_name: str, raw: dict) -> dict:
    out = {}
    for f in cfg["fields"]:
        cols = f["sources"].get(source_name) or f["sources"].get("*") or []
        s = " ".join(str(raw.get(c, "") or "") for c in cols).strip()
        for op in f.get("ops", []):
            s = OPS[op](s, cfg)
        out[f["name"]] = s
    return out
