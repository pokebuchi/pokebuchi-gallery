# -*- coding: utf-8 -*-
"""
build.py ―― 写真を最適化して、ギャラリーのデータを作る

  作品リスト.csv ＋ カテゴリ.csv ＋ 写真フォルダ
        ↓
  docs/data/works.json ＋ docs/images/

写真が入っていない作品はギャラリーに出ません。
「写真を入れる」画面から呼ばれるほか、単独でも実行できます。

    py build.py
"""

import csv
import re
import json
import hashlib
import pathlib
import datetime
import unicodedata

import os
import time
import shutil
import tempfile
import subprocess

from PIL import Image, ImageOps

# iPhone の HEIC を読む方法を用意する。
#   1. pillow-heif が使えればそれを使う
#   2. 使えない場合は Windows 内蔵の変換機能（HEIF Image Extension）を使う
HEICをPILで読める = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEICをPILで読める = True
except Exception:
    pass


def _windowsでHEICを変換(元: pathlib.Path):
    """
    Windows の画像機能を使って HEIC を PNG に変換する。
    変換できたら一時ファイルのパス、無理なら None を返す。
    """
    if os.name != "nt":
        return None
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return None

    出力 = pathlib.Path(tempfile.gettempdir()) / f"pokebuchi_{os.getpid()}_{元.stem}.png"
    命令 = (
        "Add-Type -AssemblyName PresentationCore;"
        f"$s=[System.IO.File]::OpenRead('{元}');"
        "$d=[System.Windows.Media.Imaging.BitmapDecoder]::Create("
        "$s,'None','OnLoad');"
        "$e=New-Object System.Windows.Media.Imaging.PngBitmapEncoder;"
        "$e.Frames.Add($d.Frames[0]);"
        f"$o=[System.IO.File]::Create('{出力}');"
        "$e.Save($o);$o.Close();$s.Close();"
    )
    try:
        r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-STA", "-Command", 命令],
                           capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    return 出力 if (r.returncode == 0 and 出力.exists() and 出力.stat().st_size) else None


def _整える(im):
    """向きを直して RGB にする。色の情報（カラープロファイル）は保持する。

    iPhone の写真は Display P3 という広い色域で記録されている。
    この情報を落とすと、ブラウザが標準の sRGB と解釈してしまい、
    元の写真より色あせて見える。だから最後まで持ち回る。
    """
    icc = im.info.get("icc_profile")
    出来上がり = ImageOps.exif_transpose(im).convert("RGB")
    出来上がり.load()
    if icc:
        出来上がり.info["icc_profile"] = icc
    return 出来上がり


def 写真を開く(p: pathlib.Path):
    """写真を開いて、向きを直した RGB 画像を返す"""
    try:
        im = Image.open(p)
        im.load()
        return _整える(im)
    except Exception:
        pass

    変換 = _windowsでHEICを変換(p)
    if 変換 is None:
        raise ValueError("この形式の写真は読み込めませんでした")
    try:
        with Image.open(変換) as im:        # with で確実にファイルを閉じる
            im.load()
            return _整える(im)
    finally:
        for _ in range(10):                # 掴まれていたら少し待って消す
            try:
                変換.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.1)

HERE = pathlib.Path(__file__).parent
作品リスト = HERE / "作品リスト.csv"
カテゴリ表 = HERE / "カテゴリ.csv"
写真 = HERE / "写真"
DOCS = HERE / "docs"
キャッシュ = HERE / ".build_cache.json"

連結カテゴリ = "連結フレーム"
拡張子 = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".JPG", ".JPEG", ".PNG")

サムネ幅 = 600      # 一覧用
拡大幅 = 1600      # 拡大表示用


# ------------------------------------------------------------ ファイル名

def 使える名前に(s):
    """Windows のファイル名に使えない文字を置き換える"""
    s = unicodedata.normalize("NFC", (s or "").strip())
    # 「ケロマツ / ゲコガシラ」→「ケロマツ・ゲコガシラ」（前後の空白ごと置き換える）
    s = re.sub(r"\s*[/\\]\s*", "・", s)
    for 元, 先 in ((":", "："), ("*", "＊"), ("?", "？"), ('"', "”"),
                   ("<", "＜"), (">", "＞"), ("|", "｜")):
        s = s.replace(元, 先)
    return re.sub(r"\s+", " ", s).strip(" .")


def 写真の置き場所(行):
    """(フォルダ名, ファイル名の本体) を返す"""
    パック群 = [p.strip() for p in 行["パック名"].split("/") if p.strip()]
    if 連結カテゴリ in パック群:
        フォルダ = 連結カテゴリ
    else:
        フォルダ = パック群[0] if パック群 else "その他"

    番号 = (行["ナンバー"] or "").split("／")[0].strip().replace("/", "-")
    本体 = 使える名前に(行["カード名"]) or 番号
    return フォルダ, f"{本体}_{番号}" if 番号 else 本体


def 写真をさがす(フォルダ, 本体):
    for e in 拡張子:
        p = 写真 / フォルダ / (本体 + e)
        if p.exists():
            return p
    # 大文字小文字や表記ゆれに備えて、フォルダ内を総当たりでも見る
    d = 写真 / フォルダ
    if d.is_dir():
        めあて = unicodedata.normalize("NFC", 本体).lower()
        for p in d.iterdir():
            if p.is_file() and unicodedata.normalize("NFC", p.stem).lower() == めあて:
                return p
    return None


# ------------------------------------------------------------ 画像の最適化

def 指紋(p):
    st = p.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


def 画像を作る(元, 出力名, cache):
    """
    元の写真から、一覧用と拡大用の WebP を作る。
    前回と同じ写真なら作り直さない。
    戻り値は (サムネ相対パス, 拡大相対パス, 幅, 高さ)
    """
    キー = str(元.relative_to(HERE))
    印 = 指紋(元)
    前回 = cache.get(キー)
    thumb = DOCS / "images/thumb" / (出力名 + ".webp")
    large = DOCS / "images/large" / (出力名 + ".webp")

    if 前回 and 前回["印"] == 印 and 前回["名"] == 出力名 \
            and thumb.exists() and large.exists():
        return (f"images/thumb/{出力名}.webp", f"images/large/{出力名}.webp",
                前回["w"], 前回["h"])

    im = 写真を開く(元)                          # 向きの補正もここで済ませる
    w, h = im.size
    icc = im.info.get("icc_profile")             # 縮小すると消えるので控えておく

    for 幅, 出力 in ((サムネ幅, thumb), (拡大幅, large)):
        出力.parent.mkdir(parents=True, exist_ok=True)
        r = im if im.width <= 幅 else im.resize(
            (幅, round(im.height * 幅 / im.width)), Image.LANCZOS)
        r.save(出力, "WEBP", quality=82, method=5, icc_profile=icc)

    cache[キー] = {"印": 印, "名": 出力名, "w": w, "h": h}
    return (f"images/thumb/{出力名}.webp", f"images/large/{出力名}.webp", w, h)


# ---------------------------------------------------------------- カテゴリ

def カテゴリ表を読む():
    """
    カテゴリ.csv を、書かれている順番のまま読む。

    ギャラリーの並び順は、このファイルの行の順番そのもの。
      ・カテゴリの並び  … そのカテゴリが最初に出てくる行の位置
      ・パックの並び    … 同じカテゴリの中での行の位置
    「写真を入れる」画面のカテゴリタブから並べ替えられる。
    """
    行たち = []
    if カテゴリ表.exists():
        with カテゴリ表.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                パック = (r.get("パック名") or "").strip()
                if パック:
                    行たち.append({
                        "パック名": パック,
                        "カテゴリ": (r.get("カテゴリ") or "その他").strip(),
                        "型番": (r.get("型番") or "").strip(),
                    })
    return 行たち


def カテゴリを読む():
    """パック名 → (カテゴリ, 何番目か)"""
    表 = {}
    for 順, r in enumerate(カテゴリ表を読む()):
        表[r["パック名"]] = (r["カテゴリ"], 順)
    return 表


def 型番の分類(型番):
    """型番から、ギャラリーの大カテゴリを決める（初回投入.py と同じ規則）"""
    t = (型番 or "").upper()
    if t.startswith("SV"):
        return "スカーレット＆バイオレット"
    if t.startswith("M") and not t.startswith("SM"):
        return "MEGA"
    return "その他"


def カテゴリ表に加える(パック名, カテゴリ, 型番=""):
    """
    カテゴリ.csv に、そのカテゴリの先頭として1行加える。
    新しいパックは「いちばん新しいもの」とみなして上に置く。
    """
    行たち = カテゴリ表を読む()
    if any(r["パック名"] == パック名 for r in 行たち):
        return False

    新 = {"パック名": パック名, "カテゴリ": カテゴリ, "型番": 型番}
    位置 = len(行たち)
    for i, r in enumerate(行たち):
        if r["カテゴリ"] == カテゴリ:
            位置 = i
            break
    行たち.insert(位置, 新)
    カテゴリ表を書く(行たち)
    return True


def カテゴリ表を書く(行たち):
    with カテゴリ表.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["パック名", "カテゴリ", "型番"])
        for r in 行たち:
            w.writerow([r["パック名"], r["カテゴリ"], r.get("型番", "")])


# ---------------------------------------------------------------------- 本体

def main():
    if not 作品リスト.exists():
        print("作品リスト.csv が見つかりません。先に 初回投入.py を実行してください。")
        return

    分類 = カテゴリを読む()
    cache = {}
    if キャッシュ.exists():
        try:
            cache = json.loads(キャッシュ.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    with 作品リスト.open(encoding="utf-8-sig", newline="") as f:
        行たち = [r for r in csv.DictReader(f) if (r.get("カード名") or "").strip()]

    作品, 写真待ち, 使った名前 = [], [], set()

    for 行 in 行たち:
        # カテゴリ.csv に無いパックが出てきたら、その場で登録しておく
        型番 = (行.get("ナンバー") or "").split("／")[0].strip().split(" ")[0]
        for p in [x.strip() for x in (行.get("パック名") or "").split("/") if x.strip()]:
            if p not in 分類:
                かて = 連結カテゴリ if p == 連結カテゴリ else 型番の分類(型番)
                if カテゴリ表に加える(p, かて, 型番):
                    print(f"新しいパック «{p}» を {かて} に登録しました")
                分類 = カテゴリを読む()

        フォルダ, 本体 = 写真の置き場所(行)
        元 = 写真をさがす(フォルダ, 本体)

        if 元 is None:
            写真待ち.append((フォルダ, 本体, 行["カード名"]))
            continue

        出力名 = hashlib.sha1(f"{フォルダ}/{本体}".encode("utf-8")).hexdigest()[:12]
        使った名前.add(出力名)
        try:
            thumb, large, w, h = 画像を作る(元, 出力名, cache)
        except Exception as e:
            print(f"  ! {元.name} を読み込めませんでした（{e}）")
            continue

        パック群 = [p.strip() for p in 行["パック名"].split("/") if p.strip()]
        # 連結フレームは「連結フレーム」だけに置く。
        # MEGA やスカーレット＆バイオレットの中には混ぜない。
        if 連結カテゴリ in パック群:
            パック群 = [連結カテゴリ]

        作品.append({
            "name": (行["カード名"] or "").strip(),
            "packs": パック群,
            "thumb": thumb,
            "large": large,
            "w": w, "h": h,
            "url": (行.get("商品URL") or "").strip(),
            "soldOut": (行.get("完売") or "").strip().upper() in ("TRUE", "1", "○", "はい"),
        })

    # 「すべて」で見たときも、カテゴリの順に並ぶようにする。
    #   1. カテゴリの順（連結フレーム → MEGA → …）
    #   2. そのカテゴリの中でのパックの順
    #   3. 作品リスト.csv に書かれている順（＝カード番号順）
    分類 = カテゴリを読む()
    カテゴリ順 = {}
    for r in カテゴリ表を読む():
        カテゴリ順.setdefault(r["カテゴリ"], len(カテゴリ順))

    for 番, a in enumerate(作品):
        主 = a["packs"][0] if a["packs"] else ""
        かて, パック位置 = 分類.get(主, ("その他", 9999))
        a["_順"] = (カテゴリ順.get(かて, 9999), パック位置, 番)
    作品.sort(key=lambda a: a["_順"])
    for a in 作品:
        del a["_順"]

    # カテゴリの組み立て。
    # 並び順は カテゴリ.csv の行の順番そのまま。作品が1つも無いパックは出さない。
    ある = set()
    for a in 作品:
        ある.update(a["packs"])

    series, 位置 = [], {}
    for r in カテゴリ表を読む():
        かて, パック = r["カテゴリ"], r["パック名"]
        if かて not in 位置:
            位置[かて] = len(series)
            series.append({"name": かて, "packs": []})
        # カテゴリ名と同じ名前のパック（連結フレーム）は下位に出さない
        if パック in ある and パック != かて:
            series[位置[かて]]["packs"].append(パック)

    # 作品が1件も無いカテゴリは出さない
    series = [s for s in series
              if s["packs"] or s["name"] in ある]

    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    (DOCS / "data/works.json").write_text(json.dumps({
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "series": series,
        "works": 作品,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 使わなくなった画像を片づける
    消した = 0
    for d in ("images/thumb", "images/large"):
        p = DOCS / d
        if p.is_dir():
            for f in p.iterdir():
                if f.suffix == ".webp" and f.stem not in 使った名前:
                    f.unlink()
                    消した += 1

    キャッシュ.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    print(f"公開する作品 : {len(作品)} 件")
    print(f"写真待ち     : {len(写真待ち)} 件")
    if 消した:
        print(f"不要な画像を {消した} 枚削除しました")

    return 写真待ち


if __name__ == "__main__":
    main()
