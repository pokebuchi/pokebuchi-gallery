# -*- coding: utf-8 -*-
"""
写真を入れる画面（ローカル専用）

  py tool/server.py

自分のパソコンの中だけで動きます。外部には公開されません。
ブラウザで http://localhost:8080 を開いてお使いください。
"""

import csv
import io
import re
import json
import sys
import time
import uuid
import socket
import ipaddress
import collections
import pathlib
import mimetypes
import subprocess
import urllib.parse
import urllib.request
import http.server
import socketserver

from PIL import Image, ImageOps

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import build as B                       # noqa: E402  画像処理などを共用する

# 黒い画面を出さずに動かす（pythonw）と、出力先そのものが存在しない。
# そのままだと print を使っている箇所すべてが落ちるので、捨て場所を用意しておく。
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def 知らせる(*a):
    """画面があるときだけ表示する"""
    try:
        sys.stdout.write(" ".join(str(x) for x in a) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
UI = HERE
PORT = 8080                       # 変えない。スマホのホーム画面から開けなくなるため
HOSTNAME = socket.gethostname().split(".")[0].lower()

# ドロップされた写真をいったん置いておく場所。
# HEIC はブラウザで表示できないので、ここで小さな確認用画像を作る。
一時 = ROOT / "_取り込み中"
一時台帳 = 一時 / "台帳.json"

受け付ける拡張子 = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
上限バイト = 60 * 1024 * 1024      # 写真1枚あたりの上限（iPhoneの写真は3〜5MB）


def 枠の中か(場所, 親):
    """
    その場所が、決められたフォルダの中に収まっているか確かめる。

    ブラウザから送られた文字列をそのまま場所に使うと「../../」のような
    指定で枠の外に出られてしまうため、最後に必ずここを通す。
    """
    try:
        場所 = pathlib.Path(場所).resolve()
        親 = pathlib.Path(親).resolve()
        return 場所 == 親 or 親 in 場所.parents
    except Exception:
        return False


def 行を取り出す(番):
    """作品リストの n 行目を返す。範囲外なら None。"""
    if not isinstance(番, int) or 番 < 0:
        return None
    with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
        行たち = list(csv.DictReader(f))
    return 行たち[番] if 番 < len(行たち) else None


def その作品の置き場所(番):
    """
    行番号から、写真の置き場所を組み立てる。

    フォルダ名やファイル名をブラウザから受け取らないので、
    枠の外を指定される余地そのものが無くなる。
    """
    行 = 行を取り出す(番)
    if 行 is None:
        return None
    フォルダ, 本体 = B.写真の置き場所(行)
    if not 枠の中か(B.写真 / フォルダ / (本体 + ".jpg"), B.写真):
        return None
    return フォルダ, 本体


def 整える(s):
    """全角半角や空白のゆれを吸収してから見比べる"""
    return re.sub(r"\s+", " ", (s or "").strip().translate(
        str.maketrans("０１２３４５６７８９／　", "0123456789/ "))).upper()


def 番号の値(ナンバー):
    """「M5 082/081」から 82 を取り出す。番号が無ければ -1。"""
    m = re.search(r"(\d+)\s*/", ナンバー or "")
    return int(m.group(1)) if m else -1


def 作品の並びを直す(順番):
    """
    作品リスト.csv の中で、指定された行だけを並べ替える。
    もともとそれらの行があった場所に、新しい順で入れ直す。
    他の作品の位置は動かさない。
    """
    if not 順番:
        return 0
    with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
        行たち = list(csv.DictReader(f))

    使える = [i for i in 順番 if 0 <= i < len(行たち)]
    枠 = sorted(使える)
    if len(枠) != len(set(使える)) or not 枠:
        return 0
    if [行たち[i] for i in 枠] == [行たち[i] for i in 使える]:
        return 0                       # 並びが変わっていない

    差し替え = [行たち[i] for i in 使える]
    for 置き場, 中身 in zip(枠, 差し替え):
        行たち[置き場] = 中身

    with B.作品リスト.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, ["ナンバー", "カード名", "パック名", "商品URL"])
        w.writeheader()
        w.writerows(行たち)
    return len(枠)


def 連結の先頭(行たち):
    """連結フレームの作品が並び始める位置。新しいものはここに入れる。"""
    for i, 行 in enumerate(行たち):
        if "連結フレーム" in [p.strip() for p in
                              (行.get("パック名") or "").split("/")]:
            return i
    return len(行たち)


def 仲間の場所をさがす(行たち, ナンバー, パック名):
    """
    同じパックの作品がどこに並んでいるかを見て、
    カード番号の順に収まる位置を返す。
    そのパックがまだ1件も無ければ、同じ型番の隣、それも無ければ末尾。
    """
    値 = 番号の値(ナンバー)

    # まず同じパックの作品をさがす
    仲間 = [i for i, 行 in enumerate(行たち)
            if パック名 in [p.strip() for p in (行.get("パック名") or "").split("/")]]

    # 見つからなければ、型番が書かれていればそれを手がかりにする
    if not 仲間:
        型番 = (ナンバー or "").split(" ")[0].upper()
        if 型番 and not 型番[0].isdigit():
            仲間 = [i for i, 行 in enumerate(行たち)
                    if (行.get("ナンバー") or "").split(" ")[0].upper() == 型番]
    if not 仲間:
        return len(行たち)

    for i in 仲間:
        if 番号の値(行たち[i].get("ナンバー")) > 値:
            return i
    return 仲間[-1] + 1


# ------------------------------------------------------ 取り込み中の写真

def 台帳を読む():
    if 一時台帳.exists():
        try:
            return json.loads(一時台帳.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def 台帳を書く(d):
    一時.mkdir(parents=True, exist_ok=True)
    一時台帳.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def 一時の写真(id_, 台帳=None):
    台帳 = 台帳 if 台帳 is not None else 台帳を読む()
    情報 = 台帳.get(id_)
    if not 情報:
        return None
    p = 一時 / (id_ + 情報["ext"])
    return p if p.exists() else None


def 一時を消す(id_):
    台帳 = 台帳を読む()
    情報 = 台帳.pop(id_, None)
    if 情報:
        (一時 / (id_ + 情報["ext"])).unlink(missing_ok=True)
        (一時 / (id_ + "_p.webp")).unlink(missing_ok=True)
        台帳を書く(台帳)


# ------------------------------------------------------------ 作品の一覧

def 作品一覧():
    """作品リスト.csv を読み、写真が入っているかどうかを添えて返す"""
    if not B.作品リスト.exists():
        return []

    分類 = B.カテゴリを読む()

    # ギャラリーと同じ順に並べるための下ごしらえ
    # （カテゴリの順 → その中のパックの順 → 作品リストの行の順）
    カテゴリ順 = {}
    for r in B.カテゴリ表を読む():
        カテゴリ順.setdefault(r["カテゴリ"], len(カテゴリ順))

    出力 = []
    with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
        for i, 行 in enumerate(csv.DictReader(f)):
            名前 = (行.get("カード名") or "").strip()
            if not 名前:
                continue
            フォルダ, 本体 = B.写真の置き場所(行)
            元 = B.写真をさがす(フォルダ, 本体)

            パック群 = [p.strip() for p in (行.get("パック名") or "").split("/") if p.strip()]
            主 = B.連結カテゴリ if B.連結カテゴリ in パック群 else (パック群[0] if パック群 else "")
            かて, パック位置 = 分類.get(主, ("その他", 9999))
            出力.append({
                "_順": (カテゴリ順.get(かて, 9999), パック位置, i),
                "i": i,
                "name": 名前,
                "packs": パック群,
                "mainPack": 主,
                "category": かて,
                "number": (行.get("ナンバー") or "").strip(),
                "url": (行.get("商品URL") or "").strip(),
                "isRenketsu": B.連結カテゴリ in パック群,
                "folder": フォルダ,
                "base": 本体,
                "hasPhoto": 元 is not None,
                "preview": f"/preview/{i}" if 元 else None,
            })

    出力.sort(key=lambda a: a["_順"])
    for a in 出力:
        del a["_順"]
    return 出力


def カテゴリの構成():
    """
    カテゴリ.csv を、画面で扱いやすい形に組み直す。
    ファイルの行の順番が、そのままギャラリーの並び順になる。

    連結フレームのように下位パックを持たないカテゴリは、
    代わりに作品そのものを並べ替えられるよう works を添える。
    """
    件数 = collections.Counter()
    作品 = collections.defaultdict(list)
    if B.作品リスト.exists():
        with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
            for i, 行 in enumerate(csv.DictReader(f)):
                名 = (行.get("カード名") or "").strip()
                if not 名:
                    continue
                for p in (行.get("パック名") or "").split("/"):
                    p = p.strip()
                    if p:
                        件数[p] += 1
                        作品[p].append({"i": i, "name": 名})

    まとめ, 位置 = [], {}
    for r in B.カテゴリ表を読む():
        かて, パック = r["カテゴリ"], r["パック名"]
        if かて not in 位置:
            位置[かて] = len(まとめ)
            まとめ.append({"name": かて, "packs": [], "count": 0, "works": []})
        枠 = まとめ[位置[かて]]
        自分自身 = (パック == かて)
        枠["packs"].append({"name": パック, "count": 件数.get(パック, 0),
                            "isSelf": 自分自身})
        枠["count"] += 件数.get(パック, 0)
        if 自分自身:
            枠["works"] = 作品.get(パック, [])
    return まとめ


# ------------------------------------------------------------ リクエスト処理

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, *a):
        pass

    # 同じ Wi-Fi（と自分の Tailscale）の中からだけ受け付ける
    def 送り主は身内か(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except Exception:
            return False
        if ip.is_private or ip.is_loopback:
            return True
        # Tailscale の範囲。自分のアカウントに入っている端末しか届かない
        return ip in ipaddress.ip_network("100.64.0.0/10")

    def 宛先は正しいか(self):
        """
        「どの名前でこのPCを呼んだか」を確かめる。

        悪意のあるサイトが、自分のドメイン名をこのPCのアドレスに
        すり替えて忍び込む手口（DNSリバインディング）を防ぐ。
        """
        宛先 = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if not 宛先:
            return False
        if 宛先 in ("localhost", "127.0.0.1", "::1", HOSTNAME, HOSTNAME + ".local"):
            return True
        try:                                   # 数字のアドレスで呼ばれた場合
            ipaddress.ip_address(宛先)
            return True
        except ValueError:
            return False

    def 送り元は自分自身か(self):
        """
        別のサイトの画面から呼び出されていないか確かめる。
        Origin が付いていて、それが自分自身でなければ拒否する。
        """
        送り元 = self.headers.get("Origin")
        if not 送り元:
            return True                        # 同じ画面からの通信には付かないことがある
        try:
            名 = urllib.parse.urlparse(送り元).hostname or ""
        except Exception:
            return False
        名 = 名.lower()
        if 名 in ("localhost", "127.0.0.1", "::1", HOSTNAME, HOSTNAME + ".local"):
            return True
        try:
            ipaddress.ip_address(名)
            return True
        except ValueError:
            return False

    def handle_one_request(self):
        if not self.送り主は身内か():
            try:
                self.send_error(403)
            except Exception:
                pass
            self.close_connection = True
            return
        super().handle_one_request()

    def 操作を許してよいか(self, 道):
        """
        操作（/api/ ではじまるもの）を実行してよいか判断する。

        よそのサイトからは、独自のヘッダーを付けた通信を送れない。
        送ろうとするとブラウザが事前確認をしてきて、こちらが断る。
        これで「別のサイトを開いただけで作品が消える」経路をふさぐ。
        """
        if not 道.startswith("/api/"):
            return True
        if self.headers.get("X-Pokebuchi") != "1":
            return False
        return self.宛先は正しいか() and self.送り元は自分自身か()

    # ---- 返信のしかた

    def 返す(self, データ, 種類="application/json; charset=utf-8", code=200):
        if isinstance(データ, (dict, list)):
            データ = json.dumps(データ, ensure_ascii=False).encode("utf-8")
        elif isinstance(データ, str):
            データ = データ.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", 種類)
        self.send_header("Content-Length", str(len(データ)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(データ)

    def ファイルを返す(self, p: pathlib.Path):
        if not p.is_file():
            return self.返す({"error": "not found"}, code=404)
        種類 = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".js":
            種類 = "text/javascript; charset=utf-8"
        if p.suffix == ".css":
            種類 = "text/css; charset=utf-8"
        if p.suffix == ".html":
            種類 = "text/html; charset=utf-8"
        self.返す(p.read_bytes(), 種類)

    # ---- GET

    def do_OPTIONS(self):
        # よそのサイトからの事前確認。許可を返さないので通信は成立しない
        self.send_error(403)

    def do_GET(self):
        道 = urllib.parse.urlparse(self.path).path
        if not self.宛先は正しいか():
            return self.send_error(403)
        if not self.操作を許してよいか(道):
            return self.返す({"error": "この操作は許可されていません"}, code=403)

        if 道 in ("/", "/index.html"):
            return self.ファイルを返す(UI / "ui.html")
        if 道 in ("/ui.css", "/ui.js", "/favicon.ico", "/icon-180.png"):
            return self.ファイルを返す(UI / 道.lstrip("/"))

        # スマホから開くときのアドレスを画面に伝える
        if 道 == "/api/where":
            return self.返す({
                "phone": f"{HOSTNAME}.local:{PORT}",
                "ip": (f"{このPCのアドレス()}:{PORT}"
                       if このPCのアドレス() else None),
            })

        if 道 == "/api/works":
            return self.返す({"works": 作品一覧()})

        # 作品を追加するときの選択肢（既にあるカテゴリとパック）
        if 道 == "/api/choices":
            まとめ = カテゴリの構成()
            return self.返す({
                "categories": [c["name"] for c in まとめ],
                "packs": dict((c["name"], [p["name"] for p in c["packs"]])
                              for c in まとめ),
            })

        # カテゴリの並び替え画面むけ
        if 道 == "/api/categories":
            return self.返す({"categories": カテゴリの構成()})

        # 取り込み中の写真の一覧（画面を開き直しても残る）
        if 道 == "/api/staged":
            台帳 = 台帳を読む()
            並び = sorted(台帳.items(), key=lambda x: x[1].get("t", 0))
            return self.返す({"staged": [
                {"id": i, "name": v["name"], "preview": f"/staged/{i}"}
                for i, v in 並び if 一時の写真(i, 台帳)
            ]})

        if 道.startswith("/staged/"):
            id_ = urllib.parse.unquote(道[len("/staged/"):])
            # 英数字だけに限る（「../」などで別の場所を読ませない）
            if not re.fullmatch(r"[0-9a-f]{1,32}", id_):
                return self.返す({"error": "bad id"}, code=400)
            return self.ファイルを返す(一時 / (id_ + "_p.webp"))

        # 割り当て済み写真のプレビュー（縮小済みのものを返す）
        if 道.startswith("/preview/"):
            # 置き場所はブラウザから受け取らず、作品の行番号から組み立てる
            try:
                番 = int(道[len("/preview/"):].split("/")[0])
            except ValueError:
                return self.返す({"error": "bad path"}, code=400)
            置き場所 = その作品の置き場所(番)
            if 置き場所 is None:
                return self.返す({"error": "no photo"}, code=404)
            フォルダ, 本体 = 置き場所
            名 = B.hashlib.sha1(f"{フォルダ}/{本体}".encode("utf-8")).hexdigest()[:12]
            p = B.DOCS / "images/thumb" / (名 + ".webp")
            if p.is_file():
                return self.ファイルを返す(p)
            元 = B.写真をさがす(フォルダ, 本体)      # まだ変換前なら作る
            if 元:
                try:
                    cache = self.キャッシュ読む()
                    B.画像を作る(元, 名, cache)
                    self.キャッシュ書く(cache)
                    return self.ファイルを返す(p)
                except Exception:
                    pass
            return self.返す({"error": "no photo"}, code=404)

        return self.返す({"error": "not found"}, code=404)

    # ---- POST

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        道, q = u.path, urllib.parse.parse_qs(u.query)

        if not self.操作を許してよいか(道):
            return self.返す({"error": "この操作は許可されていません"}, code=403)

        長さ = int(self.headers.get("Content-Length") or 0)
        if 長さ > 上限バイト:
            return self.返す(
                {"error": f"大きすぎます（1つあたり {上限バイト // 1024 // 1024}MB まで）"},
                code=413)

        if 道 == "/api/add":
            return self.作品を足す()
        if 道 == "/api/edit":
            return self.作品を直す()
        if 道 == "/api/delete":
            return self.作品を消す()
        if 道 == "/api/categories":
            return self.カテゴリを並べ替える()
        if 道 == "/api/stage":
            return self.取り込む(q)
        if 道 == "/api/unstage":
            一時を消す((q.get("id") or [""])[0])
            return self.返す({"ok": True})
        if 道 == "/api/unstage-all":
            for i in list(台帳を読む().keys()):
                一時を消す(i)
            return self.返す({"ok": True})
        if 道 == "/api/assign":
            return self.写真を割り当てる(q)
        if 道 == "/api/build":
            return self.まとめて作る()
        if 道 == "/api/publish":
            return self.公開する()
        return self.返す({"error": "not found"}, code=404)

    def カテゴリを並べ替える(self):
        """画面で並べ替えた順番を カテゴリ.csv に書き戻す"""
        長さ = int(self.headers.get("Content-Length") or 0)
        try:
            入力 = json.loads(self.rfile.read(長さ).decode("utf-8"))
        except Exception:
            return self.返す({"error": "入力を読み取れませんでした"}, code=400)

        元 = dict((r["パック名"], r) for r in B.カテゴリ表を読む())
        新しい行 = []
        for かて in 入力.get("categories", []):
            名 = (かて.get("name") or "").strip()
            if not 名:
                continue
            for p in かて.get("packs", []):
                パック = (p.get("name") or "").strip()
                if パック and パック in 元:
                    新しい行.append({
                        "パック名": パック,
                        "カテゴリ": 名,
                        "型番": 元[パック].get("型番", ""),
                    })

        # 画面に出ていなかったパックが消えてしまわないよう、後ろに残す
        載った = set(r["パック名"] for r in 新しい行)
        for パック名, r in 元.items():
            if パック名 not in 載った:
                新しい行.append(r)

        if not 新しい行:
            return self.返す({"error": "並び順が空でした"}, code=400)

        B.カテゴリ表を書く(新しい行)

        # 連結フレームのように、作品そのものを並べ替えたものがあれば反映する
        並べ替え = []
        for かて in 入力.get("categories", []):
            並べ替え += [w.get("i") for w in (かて.get("works") or [])
                         if isinstance(w.get("i"), int)]
        直した = 作品の並びを直す(並べ替え)

        return self.返す({"ok": True, "count": len(新しい行), "moved": 直した})

    def 作品を足す(self):
        """画面のフォームから、作品リスト.csv に1行加える"""
        長さ = int(self.headers.get("Content-Length") or 0)
        try:
            入力 = json.loads(self.rfile.read(長さ).decode("utf-8"))
        except Exception:
            return self.返す({"error": "入力を読み取れませんでした"}, code=400)

        カード名 = (入力.get("name") or "").strip()
        パック名 = (入力.get("pack") or "").strip()
        ナンバー = (入力.get("number") or "").strip()
        カテゴリ = (入力.get("category") or "").strip()
        商品URL = (入力.get("url") or "").strip()

        # 連結フレームはカード番号もパックも1つに定まらないので、
        # 入力を求めず「連結フレーム」としてまとめて扱う。
        連結か = (カテゴリ == B.連結カテゴリ)
        if 連結か:
            パック名 = B.連結カテゴリ

        いる = [("カード名", カード名), ("カテゴリ", カテゴリ)]
        if not 連結か:
            いる += [("パック名", パック名), ("カードナンバー", ナンバー)]
        足りない = [名 for 名, 値 in いる if not 値]
        if 足りない:
            return self.返す({"error": "、".join(足りない) + " を入力してください"},
                             code=400)
        if 商品URL and not 商品URL.startswith(("http://", "https://")):
            return self.返す({"error": "商品URLは https:// から始まる形で入れてください"},
                             code=400)

        列 = ["ナンバー", "カード名", "パック名", "商品URL"]
        with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
            行たち = list(csv.DictReader(f))

        # 同じパックの中に同じナンバーが既にあれば足さない。
        # （型番を省いた場合、別のパックで同じ番号が使われることがあるため
        #   ナンバーだけでなくパック名も合わせて見る）
        if ナンバー:
            for 行 in 行たち:
                同じ番号 = 整える(行.get("ナンバー")) == 整える(ナンバー)
                同じパック = パック名 in [p.strip() for p in
                                         (行.get("パック名") or "").split("/")]
                if 同じ番号 and 同じパック:
                    return self.返す(
                        {"error": f"{パック名} の {ナンバー} は "
                                  f"«{行.get('カード名')}» で既に登録されています"},
                        code=400)

        新しい行 = {
            "ナンバー": ナンバー,
            "カード名": カード名,
            "パック名": パック名,
            "商品URL": 商品URL,
        }

        if 連結か:
            # 連結フレームは番号で並べようがないので、新しいものを先頭に置く
            差し込む位置 = 連結の先頭(行たち)
        else:
            差し込む位置 = 仲間の場所をさがす(行たち, ナンバー, パック名)
        行たち.insert(差し込む位置, 新しい行)

        with B.作品リスト.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, 列)
            w.writeheader()
            w.writerows(行たち)

        # パックがまだカテゴリ.csv に無ければ、そのカテゴリの先頭に登録しておく
        型番 = ナンバー.split(" ")[0]
        B.カテゴリ表に加える(パック名, カテゴリ,
                            型番 if 型番 and not 型番[0].isdigit() else "")

        return self.返す({"ok": True, "name": カード名, "position": 差し込む位置 + 1})

    def 作品を直す(self):
        """カード名・パック名・ナンバー・商品URL を直す。
        置き場所が変わる場合は、写真も一緒に引っ越させる。"""
        長さ = int(self.headers.get("Content-Length") or 0)
        try:
            入力 = json.loads(self.rfile.read(長さ).decode("utf-8"))
        except Exception:
            return self.返す({"error": "入力を読み取れませんでした"}, code=400)

        番 = 入力.get("i")
        with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
            行たち = list(csv.DictReader(f))
        if not isinstance(番, int) or not (0 <= 番 < len(行たち)):
            return self.返す({"error": "この作品は見つかりませんでした"}, code=400)

        元の行 = dict(行たち[番])
        連結か = "連結フレーム" in (元の行.get("パック名") or "")

        カード名 = (入力.get("name") or "").strip()
        if not カード名:
            return self.返す({"error": "カード名を入力してください"}, code=400)

        商品URL = (入力.get("url") or "").strip()
        if 商品URL and not 商品URL.startswith(("http://", "https://")):
            return self.返す({"error": "商品URLは https:// から始まる形で入れてください"},
                             code=400)

        新しい行 = dict(元の行)
        新しい行["カード名"] = カード名
        新しい行["商品URL"] = 商品URL
        if not 連結か:
            パック名 = (入力.get("pack") or "").strip()
            ナンバー = (入力.get("number") or "").strip()
            if not パック名 or not ナンバー:
                return self.返す({"error": "パック名とカードナンバーを入力してください"},
                                 code=400)
            新しい行["パック名"] = パック名
            新しい行["ナンバー"] = ナンバー

        # 写真の置き場所が変わるなら、先に写真を移しておく
        旧フォルダ, 旧本体 = B.写真の置き場所(元の行)
        新フォルダ, 新本体 = B.写真の置き場所(新しい行)
        写真も動かした = False
        if (旧フォルダ, 旧本体) != (新フォルダ, 新本体):
            元写真 = B.写真をさがす(旧フォルダ, 旧本体)
            if 元写真:
                先 = B.写真 / 新フォルダ
                先.mkdir(parents=True, exist_ok=True)
                古い先 = B.写真をさがす(新フォルダ, 新本体)
                if 古い先:
                    古い先.unlink(missing_ok=True)
                元写真.replace(先 / (新本体 + 元写真.suffix))
                写真も動かした = True
            self.画像を捨てる(旧フォルダ, 旧本体)

        行たち[番] = 新しい行
        with B.作品リスト.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, ["ナンバー", "カード名", "パック名", "商品URL"])
            w.writeheader()
            w.writerows(行たち)

        if not 連結か:
            B.カテゴリ表に加える(新しい行["パック名"],
                                B.カテゴリを読む().get(新しい行["パック名"],
                                                      ("その他", 0))[0])

        return self.返す({"ok": True, "name": カード名, "movedPhoto": 写真も動かした})

    def 作品を消す(self):
        """作品を1件削除する。写真も一緒に消す。"""
        長さ = int(self.headers.get("Content-Length") or 0)
        try:
            入力 = json.loads(self.rfile.read(長さ).decode("utf-8"))
        except Exception:
            return self.返す({"error": "入力を読み取れませんでした"}, code=400)

        番 = 入力.get("i")
        with B.作品リスト.open(encoding="utf-8-sig", newline="") as f:
            行たち = list(csv.DictReader(f))
        if not isinstance(番, int) or not (0 <= 番 < len(行たち)):
            return self.返す({"error": "この作品は見つかりませんでした"}, code=400)

        消す行 = 行たち.pop(番)
        フォルダ, 本体 = B.写真の置き場所(消す行)
        写真 = B.写真をさがす(フォルダ, 本体)
        if 写真:
            写真.unlink(missing_ok=True)
        self.画像を捨てる(フォルダ, 本体)

        with B.作品リスト.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, ["ナンバー", "カード名", "パック名", "商品URL"])
            w.writeheader()
            w.writerows(行たち)

        return self.返す({"ok": True, "name": (消す行.get("カード名") or "").strip()})

    def 画像を捨てる(self, フォルダ, 本体):
        """作り置きしてある縮小画像と、その記録を消す"""
        名 = B.hashlib.sha1(f"{フォルダ}/{本体}".encode("utf-8")).hexdigest()[:12]
        for d in ("images/thumb", "images/large"):
            (B.DOCS / d / (名 + ".webp")).unlink(missing_ok=True)
        cache = self.キャッシュ読む()
        for k in [k for k in cache if cache[k].get("名") == 名]:
            cache.pop(k, None)
        self.キャッシュ書く(cache)

    def 取り込む(self, q):
        """ドロップされた写真を受け取り、確認用の小さな画像を作る"""
        名前 = (q.get("name") or ["写真"])[0]
        拡張子 = (q.get("ext") or [""])[0].lower()
        if 拡張子 not in 受け付ける拡張子:
            return self.返す({"error": f"{拡張子} は扱えない形式です"}, code=400)

        長さ = int(self.headers.get("Content-Length") or 0)
        if 長さ <= 0:
            return self.返す({"error": "写真が空です"}, code=400)

        一時.mkdir(parents=True, exist_ok=True)
        id_ = uuid.uuid4().hex[:12]
        元 = 一時 / (id_ + 拡張子)
        元.write_bytes(self.rfile.read(長さ))

        try:
            im = B.写真を開く(元)          # HEIC もここで扱えるようにしてある
            icc = im.info.get("icc_profile")
            im.thumbnail((400, 400), Image.LANCZOS)
            im.save(一時 / (id_ + "_p.webp"), "WEBP", quality=72, icc_profile=icc)
        except Exception:
            元.unlink(missing_ok=True)
            return self.返す({"error": f"{名前} は写真として読み込めませんでした"},
                             code=400)

        台帳 = 台帳を読む()
        台帳[id_] = {"name": 名前, "ext": 拡張子, "t": time.time()}
        台帳を書く(台帳)
        return self.返す({"ok": True, "id": id_, "name": 名前,
                          "preview": f"/staged/{id_}"})

    # ---- 中身

    def キャッシュ読む(self):
        if B.キャッシュ.exists():
            try:
                return json.loads(B.キャッシュ.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def キャッシュ書く(self, cache):
        B.キャッシュ.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    def 写真を割り当てる(self, q):
        """取り込み中の写真を、指定された作品の置き場所へ移す"""
        # 置き場所はブラウザから受け取らず、作品の行番号から組み立てる
        try:
            番 = int((q.get("i") or [""])[0])
        except ValueError:
            return self.返す({"error": "作品が指定されていません"}, code=400)

        置き場所 = その作品の置き場所(番)
        if 置き場所 is None:
            return self.返す({"error": "この作品は見つかりませんでした"}, code=400)
        フォルダ, 本体 = 置き場所

        id_ = (q.get("id") or [""])[0]

        台帳 = 台帳を読む()
        元 = 一時の写真(id_, 台帳)
        if 元 is None:
            return self.返す({"error": "この写真は見つかりませんでした。"
                                       "お手数ですが入れ直してください。"}, code=400)
        拡張子 = 台帳[id_]["ext"]

        先 = B.写真 / フォルダ
        先.mkdir(parents=True, exist_ok=True)

        # 同じ作品に前の写真が残っていたら消してから入れ替える
        古い = B.写真をさがす(フォルダ, 本体)
        if 古い:
            try:
                古い.unlink()
            except Exception:
                pass

        保存先 = 先 / (本体 + 拡張子)
        保存先.write_bytes(元.read_bytes())
        一時を消す(id_)

        try:
            名 = B.hashlib.sha1(f"{フォルダ}/{本体}".encode("utf-8")).hexdigest()[:12]
            cache = self.キャッシュ読む()
            cache.pop(str(保存先.relative_to(ROOT)), None)
            B.画像を作る(保存先, 名, cache)
            self.キャッシュ書く(cache)
        except Exception:
            保存先.unlink(missing_ok=True)
            return self.返す(
                {"error": "この写真は読み込めませんでした。\n"
                          "ファイルが壊れていないか確認してください。"}, code=400)

        return self.返す({"ok": True, "preview": f"/preview/{番}"})

    def まとめて作る(self):
        古い, sys.stdout = sys.stdout, io.StringIO()
        try:
            写真待ち = B.main() or []
        finally:
            記録, sys.stdout = sys.stdout.getvalue(), 古い
        return self.返す({"ok": True, "waiting": len(写真待ち), "log": 記録})

    def 公開する(self):
        古い, sys.stdout = sys.stdout, io.StringIO()
        try:
            B.main()
        finally:
            sys.stdout = 古い

        if not (ROOT / ".git").is_dir():
            return self.返す({
                "ok": False,
                "message": "まだ GitHub とつながっていません。\n"
                           "サイトのデータは作りました（docs フォルダ）。\n"
                           "公開の設定は 使い方.md をご覧ください。",
            })

        手順 = [["git", "add", "-A"],
                ["git", "commit", "-m", "作品を更新"],
                ["git", "push"]]
        記録 = []
        for cmd in 手順:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            記録.append((r.stdout or "") + (r.stderr or ""))
            if r.returncode != 0 and "nothing to commit" not in 記録[-1]:
                return self.返す({"ok": False,
                                  "message": "\n".join(記録[-2:]).strip()})
        return self.返す({"ok": True, "message": "公開しました。1〜2分で反映されます。"})


def すでに動いているか():
    """
    同じ画面がもう立ち上がっていないか確かめる。

    ポートがずれると、スマホのホーム画面に登録したアイコンから
    開けなくなってしまうので、8080 は動かさず二重起動のほうを止める。
    """
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", PORT)) != 0:
            return False
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/works",
            headers={"X-Pokebuchi": "1", "Host": "localhost"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def このPCのアドレス():
    """同じ Wi-Fi の中でこのPCを指すアドレスを調べる"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


class つなぎっぱなしサーバー(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def 画面の題名にする(文字):
    """コマンド画面の上に出る名前を日本語にする"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(文字)
    except Exception:
        pass


def 開く(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def main():
    スマホ用 = f"http://{HOSTNAME}.local:{PORT}"
    PC用 = f"http://localhost:{PORT}"

    # すでに動いていたら、二重に立ち上げず画面を開くだけにする
    if すでに動いているか():
        知らせる(f"\n  すでに動いています。画面を開きます → {PC用}\n")
        開く(PC用)
        return

    画面の題名にする("ポケぶち｜写真を入れる")

    with つなぎっぱなしサーバー(("0.0.0.0", PORT), Handler) as httpd:
        ip = このPCのアドレス()
        知らせる()
        知らせる("  ====================================================")
        知らせる("     ポケぶち　写真を入れる")
        知らせる("  ====================================================")
        知らせる()
        知らせる(f"    このパソコンで  →  {PC用}")
        知らせる(f"    スマホで        →  {スマホ用}")
        if ip:
            知らせる(f"                       （{'http://%s:%d' % (ip, PORT)} でも可）")
        知らせる()
        知らせる("    スマホから使うときは、同じ Wi-Fi につないでから")
        知らせる("    上のアドレスを Safari に入力してください。")
        知らせる()
        知らせる("  ----------------------------------------------------")
        知らせる("    同じ Wi-Fi の中からしか開けません。")
        知らせる("    外部には公開されていません。")
        知らせる()
        知らせる("    終わるときは、この画面を閉じてください。")
        知らせる("  ----------------------------------------------------")
        知らせる()

        開く(PC用)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            知らせる("\n  終了しました。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # うまく動かなかったときは、理由を出したまま画面を残す
        知らせる()
        知らせる("  ----------------------------------------------------")
        知らせる("    うまく起動できませんでした。")
        知らせる(f"    理由: {e}")
        知らせる()
        知らせる("    この画面をそのまま撮って送ってください。")
        知らせる("  ----------------------------------------------------")
        知らせる()
        try:
            input("    Enter キーを押すと閉じます ... ")
        except Exception:
            pass
        sys.exit(1)
