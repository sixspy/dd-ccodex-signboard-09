"""发布上屏（正式事务 vs 影子升正 空桩可扩展）。

signboard-09 闸口：发布摘要须匹配变更单号正则 CHG-YYYY-序号并双写可检索字段。
- 甲路径：缺合规单号整笔拒写，正式发布零增。
- 乙路径：缺单号只落影子草案，正式区零改；补号后方可升正。
- 争用：发布摘要校验互斥段（按单号）独立于 publish:{screen_id} 屏锁，败方只记争用。
"""
from __future__ import annotations

import sqlite3
import time
from fastapi import APIRouter, Header, HTTPException

from audit_svc import write_audit, write_history
from auth import current_user, require_writer
from db import connect, rows_to_list
from locks import acquire, claim_change_no_gate, release, strict_acquire
from policies import assert_playlist_ready, assert_screen_online, assert_window_open
from validators import extract_change_no, require_change_no, require_positive_int, require_title

router = APIRouter(prefix="/api/publish", tags=["publish"])

GATE_LOCK_PREFIX = "summary_gate:"
CONTENTION_ACTION = "publish.summary_gate.contention"


def _gate_key(change_no: str) -> str:
    return f"{GATE_LOCK_PREFIX}{change_no}"


def _record_contention(user: dict, gate_key: str, change_no: str) -> None:
    """败方禁止标成功，只记争用审计。"""
    write_audit(
        user["username"],
        CONTENTION_ACTION,
        {"lock_key": gate_key, "change_no": change_no, "result": "lost"},
    )


@router.get("/official")
def list_official(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT p.*, s.code screen_code, s.title screen_title,
                       pl.code playlist_code, w.code window_code
                FROM publications p
                JOIN screens s ON s.id=p.screen_id
                JOIN playlists pl ON pl.id=p.playlist_id
                JOIN time_windows w ON w.id=p.window_id
                WHERE p.official=1
                ORDER BY p.id DESC
                """
            ).fetchall()
        )
    return {"items": rows}


@router.get("/shadows")
def list_shadows(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT sh.*, s.code screen_code, pl.code playlist_code, w.code window_code
                FROM publish_shadows sh
                JOIN screens s ON s.id=sh.screen_id
                JOIN playlists pl ON pl.id=sh.playlist_id
                JOIN time_windows w ON w.id=sh.window_id
                ORDER BY sh.id DESC
                """
            ).fetchall()
        )
    return {"items": rows}


def _load_refs(conn, screen_id, playlist_id, window_id):
    screen = conn.execute("SELECT * FROM screens WHERE id=?", (screen_id,)).fetchone()
    playlist = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
    window = conn.execute("SELECT * FROM time_windows WHERE id=?", (window_id,)).fetchone()
    if not screen or not playlist or not window:
        raise HTTPException(404, "屏幕/清单/时段窗不存在")
    assert_screen_online(screen["status"])
    assert_playlist_ready(playlist["status"])
    assert_window_open(window["status"])
    return screen, playlist, window


@router.post("/official")
def publish_official(body: dict, authorization: str | None = Header(default=None)):
    """甲路径：摘要须含合规变更单号，缺号整笔拒写；同事务写入正式发布并升版本。"""
    user = current_user(authorization)
    require_writer(user)
    screen_id = require_positive_int(body.get("screen_id"), "屏幕编号")
    playlist_id = require_positive_int(body.get("playlist_id"), "清单编号")
    window_id = require_positive_int(body.get("window_id"), "时段编号")
    summary = require_title(body.get("summary") or "正式发布", "发布摘要")
    change_no = require_change_no(summary)
    layout_id = body.get("layout_id")

    gate_key = _gate_key(change_no)
    try:
        strict_acquire(gate_key, user["username"])
    except HTTPException as exc:
        _record_contention(user, gate_key, change_no)
        raise HTTPException(409, f"发布摘要校验互斥，败方仅记争用：{exc.detail}") from exc

    lock_key = f"publish:{screen_id}"
    try:
        acquire(lock_key, user["username"])
        with connect() as conn:
            _load_refs(conn, screen_id, playlist_id, window_id)
            prev = conn.execute(
                "SELECT version FROM publications WHERE screen_id=? AND official=1",
                (screen_id,),
            ).fetchone()
            version = (prev["version"] + 1) if prev else 1
            conn.execute("DELETE FROM publications WHERE screen_id=? AND official=1", (screen_id,))
            cur = conn.execute(
                "INSERT INTO publications(screen_id,playlist_id,window_id,layout_id,version,summary,change_no,status,official,published_at,published_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    screen_id,
                    playlist_id,
                    window_id,
                    layout_id,
                    version,
                    summary,
                    change_no,
                    "published",
                    1,
                    time.time(),
                    user["username"],
                ),
            )
            pid = cur.lastrowid
            try:
                # 持久闸口：单号重复消费即争用，同事务回滚保证正式区零增。
                claim_change_no_gate(change_no, user["username"], pid, conn)
            except HTTPException as exc:
                conn.rollback()
                _record_contention(user, gate_key, change_no)
                raise HTTPException(409, f"发布摘要校验互斥，败方仅记争用：{exc.detail}") from exc
            try:
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                _record_contention(user, gate_key, change_no)
                raise HTTPException(409, "发布摘要校验互斥，败方仅记争用：写库争用") from exc
        write_history("publication", pid, "publish_official", summary, user["username"], change_no)
        write_audit(
            user["username"],
            "publish.official",
            {"id": pid, "version": version, "change_no": change_no},
        )
        return {"id": pid, "version": version, "path": "official", "change_no": change_no}
    finally:
        release(lock_key, user["username"])
        release(gate_key, user["username"])


@router.post("/shadow")
def create_shadow(body: dict, authorization: str | None = Header(default=None)):
    """乙路径第一步：只写影子草案，正式区零改；单号缺失允许落草案待补。"""
    user = current_user(authorization)
    require_writer(user)
    screen_id = require_positive_int(body.get("screen_id"), "屏幕编号")
    playlist_id = require_positive_int(body.get("playlist_id"), "清单编号")
    window_id = require_positive_int(body.get("window_id"), "时段编号")
    summary = require_title(body.get("summary") or "影子发布草案", "发布摘要")
    change_no = extract_change_no(summary) or ""
    layout_id = body.get("layout_id")
    with connect() as conn:
        _load_refs(conn, screen_id, playlist_id, window_id)
        cur = conn.execute(
            "INSERT INTO publish_shadows(screen_id,playlist_id,window_id,layout_id,summary,change_no,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                screen_id,
                playlist_id,
                window_id,
                layout_id,
                summary,
                change_no,
                "open",
                time.time(),
                user["username"],
            ),
        )
        sid = cur.lastrowid
        conn.commit()
    write_history("publish_shadow", sid, "create", summary, user["username"], change_no)
    return {"id": sid, "path": "shadow", "status": "open", "change_no": change_no}


@router.patch("/shadow/{shadow_id}")
def patch_shadow(shadow_id: int, body: dict, authorization: str | None = Header(default=None)):
    """乙路径补号：更新影子摘要并同步抽取单号，正式区仍零改。"""
    user = current_user(authorization)
    require_writer(user)
    summary = require_title(body.get("summary"), "发布摘要")
    change_no = extract_change_no(summary) or ""
    with connect() as conn:
        sh = conn.execute("SELECT * FROM publish_shadows WHERE id=?", (shadow_id,)).fetchone()
        if not sh:
            raise HTTPException(404, "影子不存在")
        if sh["status"] != "open":
            raise HTTPException(409, "影子不可补号（非待升正状态）")
        conn.execute(
            "UPDATE publish_shadows SET summary=?, change_no=? WHERE id=?",
            (summary, change_no, shadow_id),
        )
        conn.commit()
    write_history(
        "publish_shadow",
        shadow_id,
        "patch_summary",
        summary,
        user["username"],
        change_no,
    )
    return {"id": shadow_id, "change_no": change_no, "status": "open"}


@router.post("/shadow/{shadow_id}/promote")
def promote_shadow(shadow_id: int, authorization: str | None = Header(default=None)):
    """乙路径升正：影子须已补合规单号；原子落正式区，单号双写。"""
    user = current_user(authorization)
    require_writer(user)
    with connect() as conn:
        sh = conn.execute("SELECT * FROM publish_shadows WHERE id=?", (shadow_id,)).fetchone()
        if not sh:
            raise HTTPException(404, "影子不存在")
        if sh["status"] != "open":
            raise HTTPException(409, "影子不可升正")
        change_no = require_change_no(sh["summary"])
        screen_id = sh["screen_id"]

    gate_key = _gate_key(change_no)
    try:
        strict_acquire(gate_key, user["username"])
    except HTTPException as exc:
        _record_contention(user, gate_key, change_no)
        raise HTTPException(409, f"发布摘要校验互斥，败方仅记争用：{exc.detail}") from exc

    lock_key = f"publish:{screen_id}"
    try:
        acquire(lock_key, user["username"])
        with connect() as conn:
            sh = conn.execute("SELECT * FROM publish_shadows WHERE id=?", (shadow_id,)).fetchone()
            _load_refs(conn, sh["screen_id"], sh["playlist_id"], sh["window_id"])
            prev = conn.execute(
                "SELECT version FROM publications WHERE screen_id=? AND official=1",
                (sh["screen_id"],),
            ).fetchone()
            version = (prev["version"] + 1) if prev else 1
            conn.execute("DELETE FROM publications WHERE screen_id=? AND official=1", (sh["screen_id"],))
            cur = conn.execute(
                "INSERT INTO publications(screen_id,playlist_id,window_id,layout_id,version,summary,change_no,status,official,published_at,published_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sh["screen_id"],
                    sh["playlist_id"],
                    sh["window_id"],
                    sh["layout_id"],
                    version,
                    sh["summary"],
                    change_no,
                    "published",
                    1,
                    time.time(),
                    user["username"],
                ),
            )
            pid = cur.lastrowid
            try:
                claim_change_no_gate(change_no, user["username"], pid, conn)
            except HTTPException as exc:
                conn.rollback()
                _record_contention(user, gate_key, change_no)
                raise HTTPException(409, f"发布摘要校验互斥，败方仅记争用：{exc.detail}") from exc
            conn.execute(
                "UPDATE publish_shadows SET status='promoted' WHERE id=?",
                (shadow_id,),
            )
            try:
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                _record_contention(user, gate_key, change_no)
                raise HTTPException(409, "发布摘要校验互斥，败方仅记争用：写库争用") from exc
        write_history(
            "publication",
            pid,
            "promote_shadow",
            f"升正影子 {shadow_id}",
            user["username"],
            change_no,
        )
        write_audit(
            user["username"],
            "publish.promote",
            {"shadow_id": shadow_id, "id": pid, "change_no": change_no},
        )
        return {"id": pid, "version": version, "path": "shadow_promote", "change_no": change_no}
    finally:
        release(lock_key, user["username"])
        release(gate_key, user["username"])
