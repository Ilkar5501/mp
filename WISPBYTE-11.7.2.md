# HiQ Battler v11.7.2

Tiny Titans now has a **3-second interval between battle starts**, down from 20 seconds. The timer begins when the match starts, so simulation and image rendering count toward it; there is no additional three-second wait after the result.

The interval is specific to Tiny Titans. AF1 retains its 20-second interval, and an AF1 battle no longer adds a wait before Tiny Titans. Running event battles still resume their saved seed instead of creating overlapping matches. Drop chances, rewards, CPU teams and all other game modes are unchanged.

Upload the complete ZIP as usual: stop the bot, back up the database, replace runtime files, keep the existing database/.env/settings, then restart with `python bot.py`. No dependency or schema changes are required. All v11.7.1 fixes and ranked PvP are retained.
