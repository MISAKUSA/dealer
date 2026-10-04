import os
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("SQLITE_PATH", "deals.sqlite3")
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    discord_id INTEGER PRIMARY KEY,
                    role_type TEXT NOT NULL CHECK(role_type IN ('buyer', 'creator'))
                );
                CREATE TABLE IF NOT EXISTS server_roles (
                    guild_id INTEGER PRIMARY KEY,
                    buyer_role_id INTEGER NOT NULL,
                    creator_role_id INTEGER NOT NULL,
                    staff_role_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leaderboard_resets (
                    guild_id INTEGER PRIMARY KEY,
                    last_deal_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leaderboard_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deals (
                    deal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    spender_id INTEGER NOT NULL,
                    host_id INTEGER NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('Premade', 'Custom', 'Session')),
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                    description TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('Pending', 'Accepted', 'Completed', 'Cancelled')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS deals_status_completed
                    ON deals(status, completed_at);
                CREATE VIEW IF NOT EXISTS leaderboards AS
                    SELECT 'all_time' AS period, NULL AS month, spender_id AS user_id,
                           'buyer' AS role_type, type, COUNT(*) AS deal_count,
                           SUM(amount_cents) AS amount_cents
                    FROM deals WHERE status = 'Completed'
                    GROUP BY spender_id, type
                    UNION ALL
                          SELECT 'monthly', strftime('%Y-%m', completed_at), spender_id,
                           'buyer', type, COUNT(*), SUM(amount_cents)
                          FROM deals WHERE status = 'Completed' AND completed_at IS NOT NULL
                          GROUP BY strftime('%Y-%m', completed_at), spender_id, type
                    UNION ALL
                    SELECT 'all_time', NULL, host_id, 'creator', type,
                           COUNT(*), SUM(amount_cents)
                    FROM deals WHERE status = 'Completed'
                    GROUP BY host_id, type
                    UNION ALL
                          SELECT 'monthly', strftime('%Y-%m', completed_at), host_id,
                           'creator', type, COUNT(*), SUM(amount_cents)
                          FROM deals WHERE status = 'Completed' AND completed_at IS NOT NULL
                          GROUP BY strftime('%Y-%m', completed_at), host_id, type;
                """
            )

    def set_roles(self, guild_id: int, buyer_id: int, creator_id: int, staff_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO server_roles VALUES (?, ?, ?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                   buyer_role_id=excluded.buyer_role_id,
                   creator_role_id=excluded.creator_role_id,
                   staff_role_id=excluded.staff_role_id""",
                (guild_id, buyer_id, creator_id, staff_id),
            )

    def get_roles(self, guild_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM server_roles WHERE guild_id = ?", (guild_id,)
            ).fetchone()

    def create_deal(
        self, guild_id: int, spender_id: int, host_id: int, deal_type: str,
        amount_cents: int, description: str,
    ) -> int:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO users VALUES (?, 'buyer') ON CONFLICT(discord_id) DO UPDATE SET role_type='buyer'",
                (spender_id,),
            )
            connection.execute(
                "INSERT INTO users VALUES (?, 'creator') ON CONFLICT(discord_id) DO UPDATE SET role_type='creator'",
                (host_id,),
            )
            cursor = connection.execute(
                """INSERT INTO deals
                   (guild_id, spender_id, host_id, type, amount_cents, description, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'Pending')""",
                (guild_id, spender_id, host_id, deal_type, amount_cents, description),
            )
            return int(cursor.lastrowid)

    def get_deal(self, deal_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM deals WHERE deal_id = ?", (deal_id,)
            ).fetchone()

    def pending_deals(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                "SELECT * FROM deals WHERE status = 'Pending'"
            ).fetchall())

    def update_status(self, deal_id: int, old_status: str, new_status: str) -> bool:
        allowed_transitions = {
            ("Pending", "Accepted"),
            ("Pending", "Cancelled"),
            ("Accepted", "Completed"),
            ("Accepted", "Cancelled"),
        }
        if (old_status, new_status) not in allowed_transitions:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE deals
                   SET status = ?, completed_at = CASE WHEN ? = 'Completed'
                       THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now') ELSE completed_at END
                   WHERE deal_id = ? AND status = ?""",
                (new_status, new_status, deal_id, old_status),
            )
            return cursor.rowcount == 1

    def reset_leaderboard(self, guild_id: int) -> None:
        with self.connect() as connection:
            last_deal_id = connection.execute(
                "SELECT COALESCE(MAX(deal_id), 0) FROM deals WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO leaderboard_resets (guild_id, last_deal_id)
                   VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET last_deal_id=excluded.last_deal_id""",
                (guild_id, last_deal_id),
            )

    def set_leaderboard_channel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO leaderboard_channels VALUES (?, ?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                   channel_id=excluded.channel_id, message_id=excluded.message_id""",
                (guild_id, channel_id, message_id),
            )

    def get_leaderboard_channel(self, guild_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM leaderboard_channels WHERE guild_id = ?", (guild_id,)
            ).fetchone()

    def leaderboard(
        self, guild_id: int, period: str, month: str, role_type: str, deal_type: str | None,
        rank_by_amount: bool = True,
        limit: int | None = 10,
    ) -> list[sqlite3.Row]:
        if role_type not in ("buyer", "creator"):
            raise ValueError("role_type must be 'buyer' or 'creator'")
        user_column = "d.spender_id" if role_type == "buyer" else "d.host_id"
        filters = [
            "d.guild_id = ?",
            "d.status = 'Completed'",
            "d.completed_at IS NOT NULL",
            "d.deal_id > COALESCE((SELECT last_deal_id FROM leaderboard_resets WHERE guild_id = d.guild_id), 0)",
        ]
        parameters: list[object] = [guild_id]
        if period == "monthly":
            filters.append("strftime('%Y-%m', d.completed_at) = ?")
            parameters.append(month)
        if deal_type:
            filters.append("d.type = ?")
            parameters.append(deal_type)
        order_by = (
            "amount_cents DESC, deal_count DESC"
            if rank_by_amount or role_type == "buyer"
            else "deal_count DESC, user_id ASC"
        )
        limit_clause = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            parameters.append(limit)
        with self.connect() as connection:
            return list(connection.execute(
                f"""SELECT {user_column} AS user_id, COUNT(*) AS deal_count,
                           SUM(d.amount_cents) AS amount_cents
                    FROM deals AS d WHERE {' AND '.join(filters)}
                    GROUP BY {user_column} ORDER BY {order_by}{limit_clause}""",
                parameters,
            ).fetchall())