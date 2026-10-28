"""Load all cogs and build v11 components without a token or Discord connection."""
from pathlib import Path
import asyncio,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

async def main():
    import bot as app
    from hiq.database import Database
    from hiq.cogs.sealed import SealedView,PackView,SealedTeamView
    from hiq.cogs.clans import WarView
    from hiq.cogs.raids import RaidSetupView
    from hiq.cogs.war_setup import WarSetupView
    from hiq.sealed_store import SealedStore
    from hiq.community_store import CommunityStore
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as temp:
        app.bot.db=Database(Path(temp)/'validate.sqlite3');await app.bot.db.init()
        async with app.bot:
            for extension in app.EXTENSIONS:
                await app.bot.load_extension(extension);print('Loaded',extension,flush=True)
            for name in ('sealed','sealedview','clanwar','inv','recycle','allcraft'):
                assert app.bot.get_command(name),name
            store=SealedStore(app.bot.db)
            row=await store.create([dict(id=i,name=str(i)) for i in (1,2)],100)
            cog=app.bot.get_cog('SealedCog')
            assert SealedView(cog,row['id'],row).is_persistent()
            assert len(PackView(cog,row,row['state']['order'][0]).children)==2
            context=SimpleNamespace(author=SimpleNamespace(id=1),channel=SimpleNamespace(id=100))
            raid=RaidSetupView(app.bot.get_cog('RaidCog'),context,[],fmt=6,difficulty='extreme')
            assert any(getattr(child,'label',None)=='3v3 unavailable for Extreme' and child.disabled for child in raid.children)
            assert all(option.value=='6' for child in raid.children if getattr(child,'row',None)==1 for option in child.options)
            assert len(raid.embed().fields)==5
            assert len(WarSetupView(app.bot.get_cog('ClanCog'),context,[]).children)==6
            # Exercise slash command serialization as a deployment preflight.
            payload=[command.to_dict(app.bot.tree) for command in app.bot.tree.get_commands()]
            assert any(c['name']=='sealed' for c in payload)
            print(f'All cogs, {len(payload)} slash commands, persistent Sealed controls and Extreme menu validated.',flush=True)

if __name__=='__main__':asyncio.run(main())
