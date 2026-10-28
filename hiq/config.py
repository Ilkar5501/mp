from __future__ import annotations

import json
import os
from dotenv import load_dotenv
from pathlib import Path
from zoneinfo import ZoneInfo

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data'
CONFIG_PATH = DATA_DIR / 'game_config.json'
DB_PATH = Path(os.getenv('HIQ_DB_PATH', ROOT / 'hiq_battler.sqlite3'))

with CONFIG_PATH.open(encoding='utf-8') as f:
    GAME_CONFIG = json.load(f)

PREFIX = os.getenv('BOT_PREFIX', GAME_CONFIG.get('prefix', ';'))
TIMEZONE = ZoneInfo(os.getenv('GAME_TIMEZONE', GAME_CONFIG.get('timezone', 'America/New_York')))
TOKEN = os.getenv('DISCORD_TOKEN', '')
POW_EMOJI_ID = int(os.getenv('POW_EMOJI_ID', '0') or 0)
QIK_EMOJI_ID = int(os.getenv('QIK_EMOJI_ID', '0') or 0)
POW_EMOJI_NAME = os.getenv('POW_EMOJI_NAME', 'pow')
QIK_EMOJI_NAME = os.getenv('QIK_EMOJI_NAME', 'qik')

MECHANICS_GUIDE_URL = os.getenv('MECHANICS_GUIDE_URL', '').strip()
