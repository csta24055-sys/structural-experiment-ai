"""GitHub Actions「G5 backup」の本体（.github/workflows/g5-backup.yml）。標準ライブラリと pynacl だけを使う。
トークン・鍵の値は表示しない。Secret への書き込みはしない。"""
import base64
import glob
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OUT = "/鋼材実験レポート用_解析結果/g5_backup"
SOURCE = "/鋼材実験レポート用"
# 長期用のリフレッシュトークン（書き込みの許可つき、2026-10-22 に取り直し）を最優先。短期のアクセストークンは最後の予備
CANDS = ["DROPBOX_REFRESH_TOKEN", "DROPBOX_WRITE_REFRESH_TOKEN", "DROPBOX_BACKUP_REFRESH_TOKEN", "DROPBOX_REFRESH_TOKEN_WRITE",
         "DROPBOX_WRITE_TOKEN"]
NAME = re.compile(r"^g5-\d{5}-[0-9a-f]{12}\.tar\.xz\.sealed$")
assert not (OUT.lower() + "/").startswith(SOURCE.lower() + "/")  # 元資料のフォルダの中には書かない


def backup_key():
    """束の鍵の組。既存の Secret から導出する（保存しない）。"""
    from nacl.public import PrivateKey
    return PrivateKey(hashlib.sha256(b"structlab-g5-backup-v1|" + os.environ["DROPBOX_APP_SECRET"].encode()).digest())


def http(url, body=None, headers=None, form=None):
    data = urllib.parse.urlencode(form).encode() if form is not None else body
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def token(name):
    raw = json.loads(http("https://api.dropbox.com/oauth2/token", form={
        "grant_type": "refresh_token", "refresh_token": os.environ["CAND_" + name],
        "client_id": os.environ["DROPBOX_APP_KEY"], "client_secret": os.environ["DROPBOX_APP_SECRET"]}))
    print(f"::add-mask::{raw['access_token']}")
    return raw["access_token"]


def api(tok, ep, arg):
    return json.loads(http(f"https://api.dropboxapi.com/2/{ep}", body=json.dumps(arg).encode(),
                           headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}))


def upload(tok, path, data):
    arg = json.dumps({"path": path, "mode": "add", "autorename": False, "mute": True}, ensure_ascii=True)
    return json.loads(http("https://content.dropboxapi.com/2/files/upload", body=data, headers={
        "Authorization": f"Bearer {tok}", "Dropbox-API-Arg": arg, "Content-Type": "application/octet-stream"}))


def download(tok, path):
    arg = json.dumps({"path": path}, ensure_ascii=True)
    return http("https://content.dropboxapi.com/2/files/download", body=b"", headers={
        "Authorization": f"Bearer {tok}", "Dropbox-API-Arg": arg, "Content-Type": "text/plain"})


def listing(tok):
    try:
        r = api(tok, "files/list_folder", {"path": OUT})
    except urllib.error.HTTPError:
        return {}
    ents = r["entries"]
    while r.get("has_more"):
        r = api(tok, "files/list_folder/continue", {"cursor": r["cursor"]})
        ents += r["entries"]
    return {e["name"]: e for e in ents if e.get(".tag") == "file"}


def writer_token():
    """書き込みのできるトークン（Secret の名前の候補の順）。小さなファイルの追加と読み戻しで確かめる。"""
    have = {n: bool(os.environ.get("CAND_" + n)) for n in CANDS}
    print("登録の有無:", json.dumps(have))
    run = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    def why(e):
        """エラーの種類だけ（Dropbox の error_summary・error。値は含まない）。"""
        try:
            j = json.loads(e.read().decode(errors="replace"))
            return j.get("error_summary") or j.get("error") or ""
        except Exception:  # noqa: BLE001
            return ""
    for n in CANDS:
        if not have[n]:
            continue
        kinds = []
        try:
            kinds.append(("リフレッシュトークン", token(n)))
        except urllib.error.HTTPError as e:
            print(f"{n}: リフレッシュトークンとしては使えない（HTTP {e.code} {why(e)}）")
            val = os.environ["CAND_" + n].strip()
            print(f"::add-mask::{val}")
            kinds.append(("アクセストークン", val))     # 登録された値がアクセストークンの場合
        for kind, tok in kinds:
            try:
                body = f"G5 の保存の書き込み確認（run {run}）。削除してかまいません。\n".encode()
                r = upload(tok, f"{OUT}/_check/write-check-{run}-{n}.txt", body)
                ok = download(tok, r["path_display"]) == body
                print(f"{n}（{kind}として）: 書き込み OK・読み戻し {'OK' if ok else '不一致'} → {r['path_display']}")
                if ok:
                    if kind == "アクセストークン":
                        print("注意: アクセストークンは数時間で失効する（長期の保存にはリフレッシュトークンが必要）")
                    return tok
            except urllib.error.HTTPError as e:
                print(f"{n}（{kind}として）: 書き込み不可（HTTP {e.code} {why(e)}）")
    sys.exit("書き込みのできるトークンがありません")


def check_all():
    """Secret の候補を 1 つずつ、成功しても止めずに確かめる（リフレッシュ・許可の種類・書き込み・読み戻し）。値は表示しない。"""
    def why(e):
        try:
            j = json.loads(e.read().decode(errors="replace"))
            return j.get("error_summary") or j.get("error_description") or j.get("error") or ""
        except Exception:  # noqa: BLE001
            return ""
    run = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    for n in CANDS:
        if not os.environ.get("CAND_" + n):
            print(f"{n}: 未登録")
            continue
        try:
            raw = json.loads(http("https://api.dropbox.com/oauth2/token", form={
                "grant_type": "refresh_token", "refresh_token": os.environ["CAND_" + n],
                "client_id": os.environ["DROPBOX_APP_KEY"], "client_secret": os.environ["DROPBOX_APP_SECRET"]}))
            tok = raw["access_token"]
            print(f"::add-mask::{tok}")
            print(f"{n}: リフレッシュ OK（許可の種類: {raw.get('scope') or '応答に含まれない'}）")
        except urllib.error.HTTPError as e:
            print(f"{n}: リフレッシュ不可（HTTP {e.code} {why(e)}）")
            continue
        try:
            api(tok, "files/list_folder", {"path": SOURCE, "limit": 1})
            print(f"{n}: 読み取り（元資料の一覧）OK")
        except urllib.error.HTTPError as e:
            print(f"{n}: 読み取り不可（HTTP {e.code} {why(e)}）")
        try:
            body = f"G5 の保存の書き込み確認（run {run}、{n}）。削除してかまいません。\n".encode()
            r = upload(tok, f"{OUT}/_check/refresh-check-{run}-{n}.txt", body)
            ok = download(tok, r["path_display"]) == body
            print(f"{n}: 書き込み OK・読み戻し {'OK' if ok else '不一致'} → {r['path_display']}")
        except urllib.error.HTTPError as e:
            print(f"{n}: 書き込み不可（HTTP {e.code} {why(e)}）")


def transfer(tok):
    have = listing(tok)
    n = 0
    for p in sorted(glob.glob("transfer/*.sealed")):
        name = os.path.basename(p)
        if not NAME.match(name):
            print(f"対象外の名前: {name}")
            continue
        data = open(p, "rb").read()
        if name in have:
            same = download(tok, f"{OUT}/{name}") == data
            print(f"既にある: {name}（{'同じ内容' if same else '内容が違う → 上書きしない'}）")
            if not same:
                sys.exit(1)
            continue
        upload(tok, f"{OUT}/{name}", data)
        if download(tok, f"{OUT}/{name}") != data:
            sys.exit(f"読み戻しが一致しない: {name}")
        print(f"保存: {name}（{len(data)} バイト、読み戻し一致）")
        n += 1
    print(f"新しく保存した束: {n}")


def restore(tok, pub_b64):
    from nacl.public import PublicKey, SealedBox
    sk = backup_key()
    dest = SealedBox(PublicKey(base64.b64decode(pub_b64)))
    os.makedirs("restore_out", exist_ok=True)
    n = 0
    for name in sorted(listing(tok)):
        if not NAME.match(name):
            continue
        plain = SealedBox(sk).decrypt(download(tok, f"{OUT}/{name}"))
        open(f"restore_out/{name}", "wb").write(dest.encrypt(plain))   # 作業環境の公開鍵で封をし直す（平文は残さない）
        n += 1
    print(f"復元用の束: {n}（作業環境の公開鍵 {hashlib.sha256(base64.b64decode(pub_b64)).hexdigest()[:16]} で封）")


def main():
    path = "ops/requests/g5-backup.txt"
    req = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    m = re.search(r"^mode:\s*(\w+)", req, re.M)
    mode = m.group(1) if m else ""
    if os.environ.get("BRANCH") == "g5-backup-transfer":
        transfer(writer_token())
    elif mode == "keygen":
        print("G5_BACKUP_PUBLIC_KEY=" + base64.b64encode(bytes(backup_key().public_key)).decode())
        writer_token()
    elif mode == "check":
        check_all()
    elif mode == "restore":
        restore(writer_token(), re.search(r"^restore_pubkey:\s*(\S+)", req, re.M).group(1))
    else:
        print(f"mode {mode!r}: 何もしない")


if __name__ == "__main__":
    main()
