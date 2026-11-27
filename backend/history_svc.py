"""变更历史。"""
from __future__ import annotations

from fastapi import APIRouter, Header

from auth import current_user
from db import connect, rows_to_list

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
def list_history(
    limit: int = 100,
    change_no: str | None = None,
    authorization: str | None = Header(default=None),
):
    """变更历史；传 change_no 时跨表联查 publications，只回已落正式区的单号记录。

    未升正的 publish_shadow 草案在 publications 无对应行，联查自动剔除，
    避免把影子当成已发布齐套。
    """
    current_user(authorization)
    safe_limit = max(1, min(limit, 300))
    with connect() as conn:
        if change_no:
            key = change_no.strip()
            rows = rows_to_list(
                conn.execute(
                    """
                    SELECT h.*
                    FROM change_history h
                    JOIN publications p
                      ON p.id = h.entity_id
                     AND h.entity_kind = 'publication'
                     AND p.official = 1
                    WHERE h.change_no = ?
                    ORDER BY h.id DESC
                    LIMIT ?
                    """,
                    (key, safe_limit),
                ).fetchall()
            )
        else:
            rows = rows_to_list(
                conn.execute(
                    "SELECT * FROM change_history ORDER BY id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
            )
    return {"items": rows}
