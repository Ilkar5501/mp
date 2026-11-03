# Restore the original Fatigue cap — v11.3.8

Stop the bot, replace VERSION, data/characters.json, hiq/battle/types.py, hiq/battle/engine.py, hiq/battle/talent.py and hiq/contest.py, then restart. Keep your database and .env. No dependency changes.

Fatigue is capped at 7 stacks as originally specified. Maximum combat penalties are Spd/Jmp -7, Pow/Qik -6 and Def/Rec -5. Stacks still persist across turns and remain cleansable. New applications and reads of oversized legacy payloads are clamped to seven. Normal combat, Clan Contest, bonus-count checks and related Fatigue consumers use the same cap. Card text and the extended tooltip are corrected; the archive website is corrected separately.

Existing scores and prizes are not changed by this update. Earlier uncapped Clan Contest simulations and team rankings are not valid evidence for the intended capped rules and must be rerun. Saved interrupted contests rerun under the corrected cap. No global stat-floor or unrelated balance changes are included.
