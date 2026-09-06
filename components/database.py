"""
Holder for database functions
"""

import aiosqlite

async def transactions(statements: str | list[str]) -> list[list[aiosqlite.Row]]:
    """
    statements: str for single statement, list[str] for multiple

    Returns a list of Rows for each statement provided

    Execute lines of sql, commit, tidy up, and return everything returned by the statements

    Try to bundle as many statements as possible into this parameter for efficiency
    
    For example if you're calling this function in a loop, wait until the loop has terminated to execute all your statements
    """
    if type(statements) == str:
        statements = [statements]
    async with aiosqlite.connect("audio.db") as db:
        db.row_factory = aiosqlite.Row
        results: list[aiosqlite.Row] = []
        for s in statements:
            if len(s) != 0:
                cur = await db.execute(s)
                results.append(await cur.fetchall())
        await db.commit()
    return results