"""
Holder for database functions

ON CONNECTIONS:

Connections are established when required but the responsibility of closing the connection lies with the function that created it
"""

import aiosqlite

async def execute(statement: str, conn: aiosqlite.Connection = None) -> list[aiosqlite.Row]:
    """
    statement: A single sqlite statement

    conn: An open connection to the database (optional)

    Execute the single statement and return all results. Tidy up if connection not provided
    """
    owner = False
    if not conn:
        conn = await connection()
        owner = True
    cur = await conn.execute(statement)
    results = await cur.fetchall()
    if owner:
        await finish(conn)
    return results

async def executeMultiple(statements: list[str], conn: aiosqlite.Connection = None) -> list[list[aiosqlite.Row]]:
    """
    statement: A single sqlite statement

    conn: An open connection to the database (optional)

    database.execute() for multiple statements
    
    Execute the single statement and return all results. Tidy up if connection not provided
    """
    owner = False
    if not conn:
        conn = await connection()
        owner = True
    results = []
    for s in statements:
        cur = await conn.execute(s)
        results.append(await cur.fetchall())
    if owner:
        await finish(conn)
    return results

async def connection() -> aiosqlite.Connection:
    """
    Sets up a database connection for you
    
    It is now your responsibility to commit and close it before the function ends
    """
    db = await aiosqlite.connect("audio.db")
    db.row_factory = aiosqlite.Row
    return db

async def finish(conn: aiosqlite.Connection) -> None:
    """
    conn: Connection to the database

    Remember to call this before the end of any function owning a connection
    """
    await conn.commit()
    await conn.close()