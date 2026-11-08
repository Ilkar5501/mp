# HiQ Battler v11.7.3

Tiny Titans (`;tt`), All For One (`;af1`) and Gauntlet (`;gn`, both Regional and National) now use **5-second cooldowns**. Tiny Titans changes from 3 to 5 seconds, AF1 from 20 to 5, and Gauntlet from 10 to 5.

Event timers remain independent and start when the battle starts; simulation and rendering count toward them. Gauntlet's existing command cooldown remains per player and shared by its Regional/National options. No extra wait is added after results.

Other modes, rosters, rewards and features are unchanged. Upload this complete runtime ZIP, preserving the existing database, .env and host settings, then restart with `python bot.py`. No schema or dependency changes are needed. This guide supersedes earlier cooldown descriptions.
