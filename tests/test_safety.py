"""ZIP爆弾・危険パス・ディスク容量・ハッシュ計算。"""
from __future__ import annotations

import os
import zipfile

import pytest

import dummy_data
from structlab.safety import (DROPBOX_BLOCK, DiskSpaceError, DropboxHasher, SafetyError, ZipLimits,
                              dropbox_content_hash, ensure_free_space, safe_extract_zip, safe_member_path)

LIMITS = ZipLimits(max_total_bytes=200 * 1024 * 1024, max_files=100, max_ratio=200)


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil", "/etc/passwd", "C:/x.txt", "a\\..\\..\\x"])
def test_dangerous_member_paths_rejected(name):
    with pytest.raises(SafetyError):
        safe_member_path(name)


def test_safe_member_path_normalizes():
    assert str(safe_member_path("./資料/./a.pdf")) == "資料/a.pdf"


def test_zip_traversal_not_extracted(tmp_path):
    z = dummy_data.make_zip(tmp_path / "t.zip", {"ok/a.txt": b"ok", "../evil.txt": b"x", "/abs.txt": b"y"})
    dest = tmp_path / "out"
    members, skipped = safe_extract_zip(z, dest, LIMITS)
    assert [m.name for m in members] == ["ok/a.txt"]
    assert len(skipped) == 2
    assert not (tmp_path / "evil.txt").exists()
    assert not os.path.exists("/abs.txt") or open("/abs.txt", "rb").read() != b"y"


def test_zip_symlink_not_extracted(tmp_path):
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    members, skipped = safe_extract_zip(z, tmp_path / "o", LIMITS)
    assert members == [] and "シンボリックリンク" in skipped[0]


def test_zip_bomb_ratio_detected(tmp_path):
    z = dummy_data.make_zip_bomb(tmp_path / "bomb.zip", mb=60)
    with pytest.raises(SafetyError, match="ZIP爆弾|上限"):
        safe_extract_zip(z, tmp_path / "o", ZipLimits(max_total_bytes=10 ** 10, max_files=10, max_ratio=200))
    assert not any((tmp_path / "o").rglob("zeros.bin"))  # 途中まで書いたファイルも消す


def test_zip_total_size_limit(tmp_path):
    z = dummy_data.make_zip(tmp_path / "big.zip", {"a.bin": os.urandom(3 * 1024 * 1024)})
    with pytest.raises(SafetyError):
        safe_extract_zip(z, tmp_path / "o", ZipLimits(max_total_bytes=1024 * 1024, max_files=10, max_ratio=1000))


def test_zip_file_count_limit(tmp_path):
    z = dummy_data.make_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(20)})
    with pytest.raises(SafetyError, match="ファイル数"):
        safe_extract_zip(z, tmp_path / "o", ZipLimits(max_total_bytes=10 ** 9, max_files=10, max_ratio=1000))


def test_cp932_zip_names(tmp_path):
    # Windows の「送る→圧縮」で作られた ZIP（ファイル名が CP932 で UTF-8 フラグなし）を再現
    z = tmp_path / "sjis.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("abcd.txt", b"x")
    sjis = "資料.txt".encode("cp932")
    assert len(sjis) == len(b"abcd.txt")
    z.write_bytes(z.read_bytes().replace(b"abcd.txt", sjis))
    members, _ = safe_extract_zip(z, tmp_path / "o", LIMITS)
    assert members[0].name == "資料.txt"


def test_disk_space_check(tmp_path):
    with pytest.raises(DiskSpaceError):
        ensure_free_space(tmp_path, 10 ** 18, 0)
    ensure_free_space(tmp_path, 1, 0)


def test_dropbox_hash_streaming_matches(tmp_path):
    data = os.urandom(DROPBOX_BLOCK * 2 + 12345)
    p = tmp_path / "x.bin"
    p.write_bytes(data)
    h = DropboxHasher()
    for i in range(0, len(data), 999_999):  # ブロック境界と一致しない分割
        h.update(data[i:i + 999_999])
    assert h.hexdigest() == dropbox_content_hash(p)


def test_dropbox_hash_known_empty(tmp_path):
    p = tmp_path / "e"
    p.write_bytes(b"")
    # 空ファイルの Dropbox content_hash は sha256(b"")
    import hashlib
    assert dropbox_content_hash(p) == hashlib.sha256(b"").hexdigest()
