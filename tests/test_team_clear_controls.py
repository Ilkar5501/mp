import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from hiq.database import Database
from hiq.community_store import CommunityStore
from hiq.event_store import EventStore
from hiq.sealed_store import SealedStore
from hiq.data import CHARACTERS
from hiq.cogs.teams import TeamCreateView,TeamEditView
from hiq.cogs.events import AF1TeamView
from hiq.cogs.sealed import SealedTeamView
from hiq.cogs.testing import BotSetView


def interaction():
    return SimpleNamespace(user=SimpleNamespace(id=1),response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),defer=AsyncMock()),edit_original_response=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))


class ClearControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'test.sqlite3')
        await self.db.init();await self.db.ensure_user(1)
        store=CommunityStore(self.db)
        async with store.tx() as c:self.cards=[await store._issue_card(c,1,'One',char) for char in CHARACTERS[:6]]
        self.bot=SimpleNamespace(db=self.db)
    async def asyncTearDown(self):self.temp.cleanup()

    async def saved(self,size=3):
        await self.db.ensure_saved_team(1,1)
        ids=[c.instance_id for c in self.cards]
        return await self.db.replace_saved_team(1,1,ids[0],ids[1],ids[2],front2_id=ids[3] if size==6 else None,front3_id=ids[4] if size==6 else None,back3_id=ids[5] if size==6 else None,team_format=size)

    def controls(self,view):
        clear=next(c for c in view.children if getattr(c,'label',None)=='Clear Team')
        confirm=next(c for c in view.children if getattr(c,'label',None)=='Confirm')
        self.assertEqual(str(clear.emoji),'🟨');self.assertEqual(clear.style,discord.ButtonStyle.secondary)
        self.assertNotEqual(clear.row,confirm.row);self.assertEqual(confirm.style,discord.ButtonStyle.success)
        self.assertTrue(all(getattr(c,'label',None) not in ('Use Team','Submit Team','Reset Team') for c in view.children))
        self.assertTrue(all(sum((c.width if hasattr(c,'width') else 1) for c in view.children if c.row==row)<=5 for row in range(5)))
        return clear,confirm

    async def test_saved_builder_clears_all_positions_and_keeps_format(self):
        for size in (3,6):
            team=await self.saved(size);view=TeamCreateView(self.bot,1,1,team.name,self.cards,team_format=size)
            clear,confirm=self.controls(view);i=interaction();await clear.callback(i)
            fresh=await self.db.get_saved_team(1,1)
            self.assertEqual(fresh.used,0);self.assertEqual(fresh.team_format,size)
            i.response.edit_message.assert_awaited_once();i.response.send_message.assert_not_awaited()
            await confirm.callback(i);self.assertIn('Complete the team',i.response.send_message.call_args.args[0])

    async def test_saved_editor_clear_is_immediate_draft_change_cancel_preserves_saved_team(self):
        for size in (3,6):
            team=await self.saved(size);view=TeamEditView(self.bot,1,team,self.cards)
            clear,_=self.controls(view);i=interaction();await clear.callback(i)
            self.assertEqual(view.draft,[None]*size)
            self.assertEqual((await self.db.get_saved_team(1,1)).card_ids,team.card_ids)
            await next(c for c in view.children if getattr(c,'label',None)=='Cancel').callback(i)
            self.assertEqual((await self.db.get_saved_team(1,1)).card_ids,team.card_ids)

    async def test_saved_editor_can_confirm_cleared_draft_or_restore_original(self):
        team=await self.saved();view=TeamEditView(self.bot,1,team,self.cards)
        clear,_=self.controls(view);await clear.callback(interaction())
        await next(c for c in view.children if getattr(c,'label',None)=='Reset Changes').callback(interaction())
        self.assertEqual(tuple(view.draft),team.card_ids)
        clear,confirm=self.controls(view);await clear.callback(interaction());await confirm.callback(interaction())
        self.assertEqual((await self.db.get_saved_team(1,1)).used,0)

    async def test_af1_clear_is_atomic_revision_checked_and_slot_scoped(self):
        store=EventStore(self.db)
        team=await store.team(1,1)
        for index,card in enumerate(self.cards):team=await store.set_entry(1,1,index,card.instance_id,'front' if index==0 else 'back',team['revision'])
        other=await store.team(1,2);other=await store.set_entry(1,2,0,self.cards[0].instance_id,'front',other['revision'])
        old=team['revision'];view=AF1TeamView(SimpleNamespace(bot=self.bot,store=store),1,team,self.cards)
        clear,_=self.controls(view);i=interaction();await clear.callback(i)
        fresh=await store.team(1,1)
        self.assertEqual(fresh['entries'],[None]*7);self.assertEqual(fresh['revision'],old+1)
        self.assertEqual((await store.team(1,2))['entries'],other['entries'])
        self.assertEqual(len(await self.db.get_cards(1)),6)
        with self.assertRaisesRegex(ValueError,'changed'):await store.set_entry(1,1,0,self.cards[0].instance_id,'front',old)
        with self.assertRaisesRegex(ValueError,'changed'):await store.clear_team(1,1,old)
        self.assertTrue(next(c for c in view.children if getattr(c,'label',None)=='Confirm').disabled)

    async def sealed(self):
        store=SealedStore(self.db);row=await store.create([dict(id=1,name='One'),dict(id=2,name='Two')],100)
        async with store.tx() as c:
            st=row['state'];row['status']=st['stage']='building'
            st['picks']={str(uid):[c.id for c in CHARACTERS[:8]] for uid in (1,2)}
            st['draft_teams']={str(uid):[c.id for c in CHARACTERS[:6]] for uid in (1,2)}
            st['team_versions']={'1':0,'2':0};st['teams']={}
            await store._save(c,row)
        return store,await store.submit(row['id'],1,0)

    async def test_sealed_clear_withdraws_confirmation_and_rejects_stale_editor(self):
        store,row=await self.sealed();original_other=list(row['state']['draft_teams']['2'])
        cog=SimpleNamespace(store=store,update_board=AsyncMock());view=SealedTeamView(cog,row,1)
        clear,_=self.controls(view);i=interaction();await clear.callback(i)
        fresh=await store.session(row['id']);self.assertNotIn('1',fresh['state']['teams'])
        self.assertEqual(fresh['state']['draft_teams']['1'],[None]*6)
        self.assertEqual(fresh['state']['draft_teams']['2'],original_other)
        self.assertEqual(fresh['state']['picks']['1'],row['state']['picks']['1'])
        with self.assertRaisesRegex(ValueError,'changed'):await store.place(row['id'],1,0,CHARACTERS[0].id,0)
        with self.assertRaisesRegex(ValueError,'changed'):await store.clear_team(row['id'],1,0)
        new_view=i.response.edit_message.call_args.kwargs['view']
        self.assertTrue(next(c for c in new_view.children if getattr(c,'label',None)=='Confirm').disabled)

    async def test_sealed_cannot_clear_after_tournament_starts(self):
        store,row=await self.sealed();await store.submit(row['id'],2,0)
        with self.assertRaisesRegex(ValueError,'phase'):await store.clear_team(row['id'],1,0)

    async def test_test_builder_clear_keeps_format_and_has_separate_confirm(self):
        for size in (3,6):
            await self.db.save_test_team(1,CHARACTERS[0].id,CHARACTERS[1].id,CHARACTERS[2].id,team_format=size)
            view=BotSetView(self.bot,1);clear,confirm=self.controls(view);i=interaction();await clear.callback(i)
            row=await self.db.get_test_team(1)
            self.assertEqual(row['team_format'],size)
            from hiq.cogs.testing import test_positions
            self.assertTrue(all(row[key] is None for _,key,_ in test_positions(size)))
            await confirm.callback(i);self.assertIn('Complete the team',i.response.send_message.call_args.args[0])

    async def test_complete_saved_and_test_builders_confirm_and_close(self):
        team=await self.saved();view=TeamCreateView(self.bot,1,1,team.name,self.cards)
        _,confirm=self.controls(view);i=interaction();await confirm.callback(i)
        self.assertTrue(view.is_finished());self.assertIsNone(i.response.edit_message.call_args.kwargs['view'])
        self.assertEqual((await self.db.active_team(1)).slot,1)
        await self.db.save_test_team(1,CHARACTERS[0].id,CHARACTERS[1].id,CHARACTERS[2].id)
        view=BotSetView(self.bot,1);_,confirm=self.controls(view);i=interaction();await confirm.callback(i)
        self.assertTrue(view.is_finished());self.assertIsNone(i.response.edit_message.call_args.kwargs['view'])

    async def test_clear_controls_keep_owner_guards(self):
        team=await self.saved();store=EventStore(self.db);af1=await store.team(1,1)
        sealed,row=await self.sealed()
        views=[TeamCreateView(self.bot,1,1,team.name,self.cards),TeamEditView(self.bot,1,team,self.cards),BotSetView(self.bot,1),AF1TeamView(SimpleNamespace(bot=self.bot,store=store),1,af1,self.cards),SealedTeamView(SimpleNamespace(store=sealed),row,1)]
        for view in views:
            i=interaction();i.user.id=99
            self.assertFalse(await view.interaction_check(i));i.response.send_message.assert_awaited_once()

    async def test_queued_clear_does_not_change_draft_during_or_after_confirmation(self):
        team=await self.saved();view=TeamEditView(self.bot,1,team,self.cards)
        clear,_=self.controls(view)
        for saving,finished in ((True,False),(False,True)):
            view.saving=saving;view.finished=finished
            await clear.callback(interaction())
            self.assertEqual(tuple(view.draft),team.card_ids)
