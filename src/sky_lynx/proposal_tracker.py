"""Pipeline config proposal tracker with Telegram squawk pattern.

Manages proposed pipeline configuration changes with a propose-then-squawk
lifecycle:

1. Sky-Lynx analysis proposes a config change (e.g., adjust threshold)
2. Proposal sent to Telegram with rationale
3. If not accepted/rejected within 24h, squawk (escalation reminder)
4. Human accepts or rejects via CLI

States: proposed → accepted | rejected | escalated
"""

import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Proposals DB lives alongside Sky-Lynx data
DEFAULT_PROPOSALS_DB = Path.home() / "projects" / "sky-lynx" / "data" / "proposals.db"

# Hours before first squawk, and between subsequent squawks
SQUAWK_INTERVAL_HOURS = 24


def _get_db_path() -> Path:
    return Path(os.environ.get("SKYLYNX_PROPOSALS_DB", str(DEFAULT_PROPOSALS_DB)))


def _get_telegram_config() -> tuple[str | None, str | None]:
    """Get Telegram bot token and chat ID from environment."""
    return (
        os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("METROPLEX_TELEGRAM_BOT_TOKEN"),
        os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("METROPLEX_TELEGRAM_CHAT_ID"),
    )


def _send_telegram(message: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    bot_token, chat_id = _get_telegram_config()
    if not bot_token or not chat_id:
        logger.info("No Telegram config — logging proposal instead")
        logger.info("[proposal] %s", message)
        return False

    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        logger.warning("Telegram send failed: %s", e)
        return False


class ProposalTracker:
    """Manages pipeline config proposals with squawk escalation."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(_get_db_path())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self._init_schema()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parameter TEXT NOT NULL,
                current_value TEXT NOT NULL,
                proposed_value TEXT NOT NULL,
                rationale TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'sky-lynx',
                status TEXT NOT NULL DEFAULT 'proposed'
                    CHECK(status IN ('proposed', 'accepted', 'rejected', 'escalated')),
                proposed_at TEXT NOT NULL,
                resolved_at TEXT,
                last_squawk_at TEXT,
                squawk_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        self.conn.commit()

    def propose(self, parameter: str, current_value: str, proposed_value: str, rationale: str) -> int:
        """Create a new proposal and notify via Telegram.

        Returns the proposal ID.
        """
        self.connect()
        now = datetime.now(timezone.utc).isoformat()

        cursor = self.conn.execute(
            "INSERT INTO proposals (parameter, current_value, proposed_value, rationale, proposed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (parameter, current_value, proposed_value, rationale, now),
        )
        self.conn.commit()
        proposal_id = cursor.lastrowid

        # Send Telegram notification
        msg = (
            f"<b>Sky-Lynx Pipeline Proposal #{proposal_id}</b>\n\n"
            f"<b>Parameter:</b> {parameter}\n"
            f"<b>Current:</b> {current_value}\n"
            f"<b>Proposed:</b> {proposed_value}\n\n"
            f"<b>Rationale:</b> {rationale}\n\n"
            f"To accept: <code>sky-lynx apply-proposal {proposal_id}</code>\n"
            f"To reject: <code>sky-lynx reject-proposal {proposal_id}</code>"
        )
        _send_telegram(msg)

        logger.info("Created proposal #%d: %s %s → %s", proposal_id, parameter, current_value, proposed_value)
        return proposal_id

    def accept(self, proposal_id: int) -> bool:
        """Accept a proposal."""
        self.connect()
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "UPDATE proposals SET status = 'accepted', resolved_at = ? "
            "WHERE id = ? AND status IN ('proposed', 'escalated')",
            (now, proposal_id),
        )
        self.conn.commit()
        if cursor.rowcount > 0:
            logger.info("Accepted proposal #%d", proposal_id)
            _send_telegram(f"Proposal #{proposal_id} accepted.")
            return True
        return False

    def reject(self, proposal_id: int) -> bool:
        """Reject a proposal."""
        self.connect()
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "UPDATE proposals SET status = 'rejected', resolved_at = ? "
            "WHERE id = ? AND status IN ('proposed', 'escalated')",
            (now, proposal_id),
        )
        self.conn.commit()
        if cursor.rowcount > 0:
            logger.info("Rejected proposal #%d", proposal_id)
            _send_telegram(f"Proposal #{proposal_id} rejected.")
            return True
        return False

    def check_and_squawk(self) -> int:
        """Check for unresolved proposals older than SQUAWK_INTERVAL_HOURS.

        Sends escalation messages and updates squawk count.

        Returns:
            Number of proposals squawked about.
        """
        self.connect()
        now = datetime.now(timezone.utc)

        # Find proposals that need squawking
        rows = self.conn.execute(
            "SELECT * FROM proposals WHERE status IN ('proposed', 'escalated')"
        ).fetchall()

        squawked = 0
        for row in rows:
            proposed_at = datetime.fromisoformat(row["proposed_at"])
            last_squawk = (
                datetime.fromisoformat(row["last_squawk_at"])
                if row["last_squawk_at"]
                else proposed_at
            )

            hours_since_last = (now - last_squawk).total_seconds() / 3600
            if hours_since_last < SQUAWK_INTERVAL_HOURS:
                continue

            # Time to squawk
            squawk_num = row["squawk_count"] + 1
            urgency = "REMINDER" if squawk_num <= 2 else "URGENT"

            msg = (
                f"<b>{urgency}: Pending Pipeline Proposal #{row['id']}</b>\n\n"
                f"<b>Parameter:</b> {row['parameter']}\n"
                f"<b>Change:</b> {row['current_value']} → {row['proposed_value']}\n"
                f"<b>Rationale:</b> {row['rationale']}\n"
                f"<b>Pending for:</b> {squawk_num * SQUAWK_INTERVAL_HOURS}h\n\n"
                f"Accept: <code>sky-lynx apply-proposal {row['id']}</code>\n"
                f"Reject: <code>sky-lynx reject-proposal {row['id']}</code>"
            )
            _send_telegram(msg)

            self.conn.execute(
                "UPDATE proposals SET status = 'escalated', last_squawk_at = ?, squawk_count = ? "
                "WHERE id = ?",
                (now.isoformat(), squawk_num, row["id"]),
            )
            squawked += 1

        self.conn.commit()
        return squawked

    def get_pending(self) -> list[dict]:
        """Get all pending/escalated proposals."""
        self.connect()
        rows = self.conn.execute(
            "SELECT * FROM proposals WHERE status IN ('proposed', 'escalated') ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_proposal(self, proposal_id: int) -> dict | None:
        """Get a single proposal by ID."""
        self.connect()
        row = self.conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None
