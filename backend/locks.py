"""写入短锁，防并发双写。"""
from __future__ import annotations

import time
from fastapi import APIRouter, Header, HTTPException

from auth import current_user, require_writer
from db import connect

router = APIRouter(prefix="/api/locks", tags=["locks"])


def acquire(lock_key: str, holder: str, ttl_sec: int = 20) -> None:
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT holder, expires_at FROM write_locks WHERE lock_key=?",
            (lock_key,),
        ).fetchone()
        if row and row["expires_at"] > now and row["holder"] != holder:
            raise HTTPException(409, f"资源锁定中：{row['holder']}")
        conn.execute(
            "INSERT INTO write_locks(lock_key,holder,expires_at) VALUES(?,?,?) "
            "ON CONFLICT(lock_key) DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at",
            (lock_key, holder, now + ttl_sec),
        )
        conn.commit()


def release(lock_key: str, holder: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM write_locks WHERE lock_key=? AND holder=?",
            (lock_key, holder),
        )
        conn.commit()


def strict_acquire(lock_key: str, holder: str, ttl_sec: int = 20) -> None:
    """争用段互斥：插入即争锁，遇任何在期持有者立刻 409（败方只记争用，禁止标成功）。

    与 acquire 的 upsert 语义不同：strict_acquire 不抢占、不重入续期，
    供「发布摘要校验互斥」等需要明确败方的独立争用段使用。
    """
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT holder, expires_at FROM write_locks WHERE lock_key=?",
            (lock_key,),
        ).fetchone()
        if row and row["expires_at"] > now:
            raise HTTPException(409, f"发布摘要校验互斥争用中：{row['holder']}")
        if row:
            conn.execute("DELETE FROM write_locks WHERE lock_key=?", (lock_key,))
        try:
            conn.execute(
                "INSERT INTO write_locks(lock_key,holder,expires_at) VALUES(?,?,?)",
                (lock_key, holder, now + ttl_sec),
            )
            conn.commit()
        except Exception as exc:
            raise HTTPException(409, "发布摘要校验互斥争用中") from exc


def claim_change_no_gate(change_no: str, holder: str, publication_id: int, conn=None) -> None:
    """发布摘要校验互斥（持久段）：一个变更单号只授权一次正式发布。

    原子插入即占闸；单号已被消费则 409，由调用方记争用审计。
    传入 conn 时与外层发布事务同库同事务（禁止另开连接，避免 SQLite 跨连接写锁）。
    与短锁不同，闸口不过期、不释放，串行/并发两种时序下败方都确定性落空。
    """
    sql = (
        "INSERT INTO change_no_gates(change_no,publication_id,holder,created_at) VALUES(?,?,?,?)"
    )
    args = (change_no, publication_id, holder, time.time())
    if conn is not None:
        try:
            conn.execute(sql, args)
        except Exception as exc:
            raise HTTPException(409, f"变更单号 {change_no} 已授权正式发布，重复上屏视为争用") from exc
        return
    with connect() as own:
        try:
            own.execute(sql, args)
            own.commit()
        except Exception as exc:
            raise HTTPException(409, f"变更单号 {change_no} 已授权正式发布，重复上屏视为争用") from exc


@router.post("/acquire")
def api_acquire(body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    key = (body.get("lock_key") or "").strip()
    if not key:
        raise HTTPException(400, "缺少 lock_key")
    acquire(key, user["username"], int(body.get("ttl_sec") or 20))
    return {"ok": True, "lock_key": key, "holder": user["username"]}


@router.post("/release")
def api_release(body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    key = (body.get("lock_key") or "").strip()
    release(key, user["username"])
    return {"ok": True}
