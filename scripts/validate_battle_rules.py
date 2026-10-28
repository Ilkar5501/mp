"""Focused battle-rule regressions; run without Discord or a database."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hiq.battle.engine import BattleEngine, floori
from hiq.battle.types import CombatContext
from hiq.data import CHARACTERS

def char(name,title=None):
    return next(c for c in CHARACTERS if c.name==name and (title is None or c.title==title))
def battle(left,right):
    def specs(names):
        return [(None,char(*n) if isinstance(n,tuple) else char(n),'front' if i<3 else 'back') for i,n in enumerate(names)]
    e=BattleEngine(specs(left),specs(right),seed=5);e.turn=1
    return e
def ctx(e,a=None,d=None):
    a=a or e.team_a.units[0];d=d or e.team_b.units[0]
    return CombatContext(e,a,d,a,d)
def quiet(e):
    for u in e.units:u.base['jmp']=100

def test_nishinoya_counter():
    e=battle(['Korai'],['Nishinoya']);c=ctx(e);n=c.defender
    assert e.can_counter(c,n,c.attacker)
    n.add_status('Cannot Counter',False)
    assert not e.can_counter(c,n,c.attacker)
    n.statuses.clear();c.attacker.add_status('Intimidation',True)
    assert not e.can_counter(c,n,c.attacker)
    e=battle(['Lev'],['Nishinoya']);c=ctx(e);c.attacker.base['spd']=100
    assert not e.can_counter(c,c.defender,c.attacker)

def test_denied_followup():
    e=battle(['Hinata'],['Koganegawa']);c=ctx(e);e.team_b.hp=int(e.team_b.max_hp*.7)
    c.defender.add_status('Cannot Counter',False)
    assert e.followup(c,c.defender,c.attacker,False)
    e.combat(c);assert c.defender.attacks_made==0

def test_brave_and_followup():
    for hp,hits in [(1,2),(.7,4)]:
        e=battle(['Hinata','Kageyama'],['Atsumu']);c=ctx(e)
        c.attacker.base['spd']=70;c.defender.base['spd']=20
        e.team_a.hp=int(e.team_a.max_hp*hp);e.team_b.hp=e.team_b.max_hp=10000
        e.combat(c);assert c.attacker.attacks_made==hits

def test_brave_reduction():
    for name in ['Korai','Komi']:
        e=battle(['Hinata','Kageyama'],[name]);c=ctx(e)
        e.team_a.hp=int(e.team_a.max_hp*.7);e.team_b.hp=e.team_b.max_hp=10000
        c.attacker.base['spd']=80;c.defender.base['spd']=10
        c.attacker.base['qik']=150;c.attacker.max_cd=c.attacker.cd=99
        c.defender.max_cd=c.defender.cd=99
        c.attacker.base['jmp']=0;c.defender.base['jmp']=100
        e.combat(c)
        damage=[int(line.split(': ')[-1].split(' damage')[0]) for line in e.log if 'Hinata →' in line]
        assert len(damage)==4 and damage[0]==damage[1] and damage[2]==damage[3] and damage[0]<damage[2],(name,damage)

def test_next_action_expiry():
    for name,stat,value in [('Bokuto','spd',-6),('Teradomari','spd',3)]:
        e=battle([name],['Hinata']);a=e.team_a.units[0]
        e.team_b.hp=e.team_b.max_hp=10000
        e.perform_action(a)
        fn=a.visible_penalty if value<0 else a.visible_bonus
        assert fn(stat)==value,(name,'expired early')
        # Stop reapplication to verify the existing effect's natural expiry.
        a.actions_taken+=1;a.expire_after_action();assert fn(stat)==0
    e=battle(['Hinata'],['Atsumu']);a=e.team_a.units[0]
    a.action_in_progress=True;a.add_status('Cannot Counter',False,through_next_action=True)
    a.add_debuff('spd',-3);a.actions_taken+=1;a.action_in_progress=False;a.expire_after_action()
    assert 'Cannot Counter' in a.statuses and a.visible_penalty('spd')==-3
    a.actions_taken+=1;a.expire_after_action();assert not a.statuses and not a.visible_debuffs

def test_dynamic_queue():
    e=battle(['Nishinoya','Kanoka'],['Korai']);e.turn=0;e.max_turns=1
    first,kan=e.team_a.units;kor=e.team_b.units[0]
    first.base['spd']=60;kan.base['spd']=36;kor.base['spd']=38
    order=[]
    def action(u,**kw):
        order.append(u.name)
        if u is first:kan.add_buff('spd',6)
        u.acted_this_turn=True
    e.perform_action=action;e.run()
    assert order==['Nishinoya','Kanoka','Korai'],order

def test_korai_restore():
    for triggered in [False,True]:
        e=battle(['Korai','Hinata'],['Atsumu']);c=ctx(e)
        ally=e.team_a.units[1];ally.lane='back';c.attacker.ult_triggered_this_combat=triggered
        e.after_combat(c);assert (ally.lane==ally.original_lane)==triggered

def test_missing_effects():
    e=battle(['Kenma'],['Hinata']);quiet(e);c=ctx(e);c.attacker.cd=0
    st=e.combat_stats(c);expected=max(0,st[c.attacker.uid][c.attacker.offense_key]-st[c.defender.uid]['def' if c.attacker.offense_key=='pow' else 'rec'])+floori(st[c.attacker.uid]['rec']*.5)
    e.resolve_hit(c,c.attacker,c.defender);assert c.attacker.damage_dealt==expected
    e=battle(['Tsuki'],['Hinata','Atsumu','Korai','Osamu']);quiet(e);c=ctx(e)
    e.team_a.hp=20;c.attacker.cd=0;e.resolve_hit(c,c.attacker,c.defender)
    assert e.team_a.hp==20+floori(c.attacker.damage_dealt*.5)
    for u in e.team_b.units:u.cd=0
    e.after_combat(c);assert [u.cd for u in e.team_b.units]==[1,1,1,0]
    e=battle(['Suga','Hinata'],['Atsumu']);e._turn_start_owner(e.team_a.units[0])
    assert all(e.team_a.units[1].visible_bonus(s)==6 for s in ['pow','qik','spd'])

def test_unnan_delay():
    e=battle(['Ushijima'],['Unnan']);c=ctx(e);c.attacker.cd=0;c.defender.base['def']=200
    e.resolve_hit(c,c.attacker,c.defender)
    assert not c.attacker.ult_triggered_this_combat
    # First hit only: once charged again, his next hit can trigger.
    e.resolve_hit(c,c.attacker,c.defender);assert c.attacker.ult_triggered_this_combat

def test_hunted_lowest():
    e=battle(['Teradomari'],['Hinata','Yamagata']);c=ctx(e);c.attacker.cd=0
    e.trigger_ultimate(c.attacker,c,'offense')
    expected=min(e.team_b.units,key=lambda u:u.noncombat_stat('def')+u.noncombat_stat('rec'))
    assert 'Hunted' in expected.statuses

def test_washio_offensive_crit():
    e=battle(['Ushijima'],['Washio']);c=ctx(e)
    a,d=c.attacker,c.defender;a.base['pow']=150;a.base['jmp']=100;d.base['jmp']=0
    a.base['def']=10;d.base['def']=40;a.cd=d.cd=99
    st=e.combat_stats(c);raw=max(0,floori(st[a.uid]['pow']*1.2)-st[d.uid]['def'])
    e.resolve_hit(c,a,d);assert a.damage_dealt==floori(raw*.6),(a.damage_dealt,raw)

def test_kai_targeting():
    for ally,lane,attacker,eligible in [('Iwaizumi','front','Hinata',True),('Komi','back','Hinata',True),('Tsuki','front','Ushijima',True),('Tsuki','back','Ushijima',False),('Iwaizumi','front','Ushijima',False)]:
        e=battle([attacker],['Kai',ally]);c=ctx(e);e.team_b.units[1].lane=lane
        st=e.combat_stats(c);gain=st[c.defender.uid]['def']-e._base_combat_stats(c.defender,c.attacker,c)['def']
        # Ushijima inflicts Def-3 in combat.
        expected=5+5*eligible-(3 if attacker=='Ushijima' else 0)
        assert gain==expected,(ally,lane,attacker,gain,expected)
        c.override_targeting=True;st=e.combat_stats(c)
        gain=st[c.defender.uid]['def']-e._base_combat_stats(c.defender,c.attacker,c)['def']
        assert gain==5-(3 if attacker=='Ushijima' else 0)

def test_aran_reon_charge():
    e=battle(['Hinata'],['Aran']);c=ctx(e);c.defender.ult_triggered_this_combat=True;c.defender.cd=3
    e.after_combat(c);assert c.defender.cd==1
    e=battle(['Hinata'],['Reon','Atsumu']);c=ctx(e)
    assert e.charge_delta(c,c.attacker,c.defender,True)==1
    c=ctx(e,d=e.team_b.units[1]);assert e.charge_delta(c,c.attacker,c.defender,True)==0
    for name,title in [('Kuroo',None),('Hinata','Miracle Maker'),('Tsuki',None)]:
        e=battle(['Atsumu'],['Kuroo']);e.team_b.units[0].character=char(name,title);c=ctx(e)
        assert e.charge_delta(c,c.defender,c.attacker,False)==2
        assert e.charge_delta(c,c.defender,c.attacker,True)==2

def test_targeting_percentages():
    for names,wanted in [(['Tsuki','Hinata','Atsumu'],[60,20,20]),(['Tsuki','Hinata','Atsumu','Osamu','Korai','Kanoka'],[46.6666667,16.6666667,16.6666667,6.6666667,6.6666667,6.6666667])]:
        e=battle(['Ushijima'],names)
        if len(names)==3:
            for u in e.team_b.units[1:]:u.lane='back'
        weights=list(e.targeting_distribution(e.team_b,'pow').values())
        assert all(abs(a-b)<.0001 for a,b in zip(weights,wanted)),weights

def test_kai_save_support():
    for saver in ['Hirugami','Nishinoya','Konoha']:
        e=battle(['Hinata'],[saver,'Kai','Atsumu'])
        actor=e.team_a.units[0];savior,kai,intended=e.team_b.units
        savior.cd=0
        actual,saved=e.maybe_save(actor,intended)
        assert saved and actual is savior
        c=CombatContext(e,actor,actual,actor,intended,save_triggered=True)
        active=e.combat_stats(c)[actual.uid]
        c.save_triggered=False
        normal=e.combat_stats(c)[actual.uid]
        assert active['def']==normal['def']+5 and active['rec']==normal['rec']+5,saver
    # Support is disabled in an isolated Fukunaga duel.
    e=battle(['Fukunaga'],['Hirugami','Kai']);c=ctx(e);c.save_triggered=True
    active=e.combat_stats(c)[c.defender.uid];c.save_triggered=False
    assert active==e.combat_stats(c)[c.defender.uid]

def test_miracle_stored_damage():
    e=battle(['Atsumu'],['Hinata'])
    h=e.team_b.units[0];h.character=char('Hinata','Miracle Maker')
    h.base={k:int(v) for k,v in h.character.stats.items()};h.lane='back'
    h.max_cd=h.character.ult_cd;h.cd=0
    a=e.team_a.units[0];quiet(e);a.base['spd']=30
    c=ctx(e)
    assert not e.can_counter(c,h,a)
    snapshot=e.combat_stats(c)[h.uid]['spd'];expected=floori(snapshot*.4)
    e.combat(c);e.after_combat(c);e.end_turn()
    assert h.stored_attack_bonus==expected and h.attacks_made==0
    # Stored amount survives a Speed change and is consumed by the next hit.
    h.base['spd']+=10
    c=CombatContext(e,h,a,h,a);st=e.combat_stats(c)
    raw=max(0,st[h.uid]['qik']-st[a.uid]['rec'])
    before=h.damage_dealt;e.resolve_hit(c,h,a)
    assert h.damage_dealt-before==raw+expected
    assert h.stored_attack_bonus==0
    before=h.damage_dealt;e.resolve_hit(c,h,a)
    assert h.damage_dealt-before==raw
    # Frontline activation grants no stored damage.
    h.lane='front';h.cd=0;c=ctx(e);e.resolve_hit(c,a,h)
    assert h.stored_attack_bonus==0

def test_defensive_charge_conditions():
    for name,title in [('Hinata','Miracle Maker'),('Kuroo',None)]:
        e=battle(['Atsumu'],['Hinata']);u=e.team_b.units[0]
        u.character=char(name,title);c=ctx(e);foe=c.attacker
        for outgoing in [False,True]:
            assert e.charge_delta(c,u,foe,outgoing)==2
            initiated=CombatContext(e,u,foe,u,foe)
            assert e.charge_delta(initiated,u,foe,outgoing)==1
    # Tsuki is HP-gated, not initiation-gated.
    e=battle(['Tsuki'],['Hinata']);c=ctx(e)
    assert e.charge_delta(c,c.attacker,c.defender,True)==2
    e.team_a.hp=int(e.team_a.max_hp*.4)
    assert e.charge_delta(c,c.attacker,c.defender,True)==1

def test_kiryu_last_hope_ultimate_and_scoping():
    e=battle([('Kiryu','Last Hope')],['Atsumu']);c=ctx(e);a,d=c.attacker,c.defender
    a.cd=0;a.base['jmp']=0;d.base['jmp']=100;d.base['def']=20
    st=e.combat_stats(c);expected=max(0,st[a.uid]['pow']-floori(st[d.uid]['def']*1.2))+35
    e.resolve_hit(c,a,d)
    assert a.damage_dealt==expected,(a.damage_dealt,expected)
    assert not any('Exposed' in u.statuses for u in e.team_b.units)
    # Last Hope allows saves; the original Kiryu still blocks them.
    for title,wanted in [('Last Hope',True),('Reluctant Titan',False)]:
        e=battle([('Kiryu',title)],['Hirugami','Atsumu'])
        actor=e.team_a.units[0];savior,target=e.team_b.units;savior.cd=0
        _,saved=e.maybe_save(actor,target);assert saved==wanted,(title,saved)

def test_kiryu_last_hope_a_and_b():
    e=battle([('Kiryu','Last Hope'),'Atsumu'],['Aone']);c=ctx(e);k,s=e.team_a.units;foe=e.team_b.units[0]
    s.lane=k.lane;foe.base['pow']=10
    st=e.combat_stats(c)
    assert st[k.uid]['pow']==k.base['pow']+4 and st[k.uid]['def']==k.base['def']+4
    assert not e.can_counter(c,foe,k,st)
    e=battle([('Kiryu','Last Hope')],['Atsumu']);c=ctx(e);k,foe=c.attacker,c.defender
    k.burden_stacks=3
    assert e.can_counter(c,foe,k) and e.followup(c,k,foe,True)
    foe.add_status('Cannot Counter',False)
    assert not e.followup(c,k,foe,True)
    # Five stacks neutralize Kiryu penalties and foe bonuses.
    foe.statuses.clear();k.burden_stacks=5;k.add_debuff('pow',-10);foe.add_buff('def',10)
    st=e.combat_stats(c)
    assert st[k.uid]['pow']==k.base['pow']+6
    assert st[foe.uid]['def']==foe.base['def']
    # Usuri doubles the end-turn gain, capped at ten.
    e=battle([('Kiryu','Last Hope'),'Usuri'],['Atsumu']);k=e.team_a.units[0]
    k.burden_stacks=9;e.end_turn();assert k.burden_stacks==10

def test_kiryu_pressure_and_recoil():
    e=battle(['Atsumu',('Kiryu','Last Hope')],['Hinata']);source=e.team_a.units[1]
    for i in range(3):
        attacker=e.team_b.units[0];defender=e.team_a.units[0]
        attacker.base['jmp']=100;defender.base['jmp']=0;attacker.cd=defender.cd=99
        c=CombatContext(e,attacker,defender,attacker,defender)
        e.resolve_hit(c,attacker,defender);e._new_unit_after_combat(c)
    assert source.burden_stacks==2
    assert e.team_b.units[0].visible_penalty('def')==-4
    # A defensive crit grants C's stack before recoil is calculated.
    e=battle([('Kiryu','Last Hope')],['Atsumu']);c=ctx(e);k,foe=c.attacker,c.defender
    k.burden_stacks=2;k.base['jmp']=0;foe.base['jmp']=100;k.cd=foe.cd=99
    before=e.team_a.hp;e.resolve_hit(c,k,foe);e._new_unit_after_combat(c)
    assert k.burden_stacks==3 and e.team_a.hp==before-3

def test_kiryu_ignores_percent_reduction():
    e=battle([('Kiryu','Last Hope')],[('Hinata','Miracle Maker')]);c=ctx(e);k,h=c.attacker,c.defender
    k.burden_stacks=5;k.base['pow']=100;k.base['jmp']=h.base['jmp']=50
    h.base['spd']=100;k.base['spd']=0;k.cd=h.cd=99
    st=e.combat_stats(c);expected=max(0,st[k.uid]['pow']-st[h.uid]['def'])
    e.resolve_hit(c,k,h);assert k.damage_dealt==expected,(k.damage_dealt,expected)

def test_kanbayashi_support():
    e=battle(['Kanbayashi','Hinata','Atsumu'],['Korai']);k,a,b=e.team_a.units
    e.team_a.hp=floori(e.team_a.max_hp*.9)
    for x in (a,b):x.base['def']=20;x.base['rec']=20;x.cd=3
    e._turn_start_owner(k)
    chosen=[x for x in (a,b) if x.visible_bonus('def')==4]
    assert len(chosen)==1 and chosen[0].cd==2
    assert k.visible_bonus('def')==4 and k.visible_bonus('rec')==4
    other=b if chosen[0] is a else a
    assert other.cd==3 and other.visible_bonus('def')==0
    # Restorative Dig triggers only on Kanbayashi's own hits.
    e=battle(['Kanbayashi'],['Atsumu']);c=ctx(e);k,foe=c.attacker,c.defender
    e.team_a.hp=e.team_a.max_hp-50;k.cd=0;k.base['rec']=40;k.base['jmp']=foe.base['jmp']=50;foe.cd=99
    before=e.team_a.hp;e.resolve_hit(c,k,foe)
    assert k.ult_triggered_this_combat and e.team_a.hp==before+30
    e=battle(['Atsumu'],['Kanbayashi']);c=ctx(e);k=c.defender;k.cd=0;c.attacker.cd=99
    e.resolve_hit(c,c.attacker,k);assert not k.ult_triggered_this_combat

def test_kanbayashi_critical_care_and_dr():
    # Two offensive crit hits still produce one six-HP after-combat heal.
    e=battle(['Atsumu','Kanbayashi'],['Suga']);kan=e.team_a.units[1];att=e.team_b.units[0];defender=e.team_a.units[0]
    att.base['jmp']=100;defender.base['jmp']=0;att.cd=defender.cd=99;e.team_a.hp=e.team_a.max_hp-100
    c=CombatContext(e,att,defender,att,defender)
    e.resolve_hit(c,att,defender);e.resolve_hit(c,att,defender)
    before=e.team_a.hp;e._new_unit_after_combat(c);assert e.team_a.hp==before+6
    # Same-row allies receive 30% DR on each offensive critical hit.
    damages=[]
    for same_row in (False,True):
        e=battle(['Ushijima'],['Atsumu','Kanbayashi']);c=ctx(e);a,d=c.attacker,c.defender;kan=e.team_b.units[1]
        d.lane='front';kan.lane='front' if same_row else 'back'
        a.base['pow']=100;a.base['jmp']=100;d.base['jmp']=0;a.cd=d.cd=99
        e.resolve_hit(c,a,d);damages.append(a.damage_dealt)
    assert damages[1]==floori(damages[0]*.7),damages

def main():
    tests=[v for k,v in globals().items() if k.startswith('test_')]
    for f in tests:f();print('PASS',f.__name__)
    print(f'{len(tests)} battle regression groups passed.')
if __name__=='__main__':main()
