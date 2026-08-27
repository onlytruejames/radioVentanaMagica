"""
Holder for database functions
"""

import aiosqlite

async def transaction(statement: str) -> list[aiosqlite.Row]:
    """
    statement: string

    Execute a line of sql, commit, tidy up, and return anything found by the statement
    """
    statements = statement.split(";")
    async with aiosqlite.connect("audio.db") as db:
        db.row_factory = aiosqlite.Row
        for s in statements:
            if len(s) != 0:
                cur = await db.execute(s + ";")
        results = await cur.fetchall()
        await db.commit()
    return results