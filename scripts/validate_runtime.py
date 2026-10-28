"""Optional deployment preflight. No tests directory or Discord login is needed."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from hiq.community_store import PREFECTURE_DAILY_CAP,PREFECTURE_WIN_REWARD
    from hiq.data import CHARACTERS,CHAR_BY_ID
    from hiq.economy import pull_pack
    from hiq.prefecture import FORMATIONS,cpu_specs
    from hiq.search import filter_characters,parse_filters
    from hiq.tournament_cpu import CPU_TEAMS,cpu_specs as tournament_cpu_specs
    if len(FORMATIONS)!=10:raise ValueError('Expected ten Prefecture CPU formations.')
    if (PREFECTURE_WIN_REWARD,PREFECTURE_DAILY_CAP)!=(50,300):raise ValueError('Unexpected Prefecture reward settings.')
    for i,formation in enumerate(FORMATIONS):
        specs=cpu_specs(i)
        if len(formation['pods'])!=3 or any(len(pod)!=3 for pod in formation['pods']):raise ValueError(f'Formation {i+1} needs three pods of three.')
        if len({c.name for _,c,_ in specs})!=9:raise ValueError(f'Formation {i+1} has duplicate player names.')
        if any(lane not in ('front','back') for _,_,lane in specs):raise ValueError(f'Formation {i+1} has an invalid lane.')
    gauntlets=json.loads((ROOT/'data/gauntlets.json').read_text())
    for mode in ('regional','national'):
        for games in gauntlets[mode]['schools'].values():
            for game in games:
                for cid in [game['front'],*game['back']]:
                    if cid not in CHAR_BY_ID:raise ValueError('Unknown Gauntlet character: '+cid)
                if not 0<=game['merge']<=10:raise ValueError('Invalid Gauntlet merge level.')

    class FixedTeam:
        def __init__(self,team):self.team=team
        def choice(self,_):return self.team
    for number,team in enumerate(CPU_TEAMS,1):
        specs=tournament_cpu_specs('validation',number,rng=FixedTeam(team))
        if len(specs)!=6:raise ValueError(f'Tournament CPU team {number} needs six characters.')

    pull_pack('position',position='Li')
    pull_pack('position',position='li')

    support_sets=[]
    for query in ('-class support','-cl support','-class sup','-cl sup','class:support','cl:sup'):
        supports=filter_characters(list(CHARACTERS),parse_filters(query,gallery=True))
        if not supports or any('support' not in c.role_class.casefold() for c in supports):
            raise ValueError(f'Gallery support-class filter failed: {query}')
        support_sets.append({c.id for c in supports})
    if any(result!=support_sets[0] for result in support_sets[1:]):
        raise ValueError('Gallery class aliases returned different results.')
    supports=filter_characters(list(CHARACTERS),parse_filters('-cl sup',gallery=True))
    hybrid=next(c for c in CHARACTERS if '/' in c.role_class and 'support' in c.role_class.casefold())
    if hybrid not in supports:raise ValueError('Multi-class unit missing from support results.')
    star_sets=[]
    for query in ('-star 5','-st 5','star:5'):
        five_stars=filter_characters(list(CHARACTERS),parse_filters(query,gallery=True))
        if not five_stars or any(c.rarity!=5 for c in five_stars):raise ValueError(f'Gallery star filter failed: {query}')
        star_sets.append({c.id for c in five_stars})
    if any(result!=star_sets[0] for result in star_sets[1:]):raise ValueError('Gallery star aliases returned different results.')
    last_hope=CHAR_BY_ID['Kiryu — Last Hope']
    for query in ('-effect guaranteed follow-up','-eff guaranteed follow-up','effect:"guaranteed follow-up"','eff:"guaranteed follow-up"'):
        if last_hope not in filter_characters([last_hope],parse_filters(query,gallery=True)):
            raise ValueError(f'Gallery effect filter did not search ability text: {query}')
    if filter_characters([last_hope],parse_filters('-eff Maximum 10 stacks',gallery=True)):
        raise ValueError('Gallery effect filter incorrectly searched extended tooltip text.')
    combined=filter_characters(list(CHARACTERS),parse_filters('-cl tank -st 4 -eff counterattack',gallery=True))
    if not combined or any('tank' not in c.role_class.casefold() or c.rarity!=4 or 'counterattack' not in ' '.join(a.text for a in c.abilities.values()).casefold() for c in combined):
        raise ValueError('Combined gallery filters failed.')

    from hiq.items import ITEMS,ITEM_BY_KEY,THEMES
    from hiq.raids import RAID_TEAMS,RAID_POOLS,cpu_specs as raid_specs
    from hiq.raid_store import RaidStore
    if len(ITEMS)!=35 or len(ITEM_BY_KEY)!=35:raise ValueError('Expected 35 unique equipment items.')
    if abs(sum(i['drop_rate'] for i in ITEMS)+.245-1)>1e-9:raise ValueError('Raid base rates must sum to 100%.')
    for theme in THEMES:
        for stage in ('easy','hard'):
            pool=RaidStore(None).reward_pool(theme,{},stage)
            if len(pool)!=9 or abs(sum(i['weight'] for i in pool)-1)>1e-9:raise ValueError('Invalid themed reward pool.')
        if any(len(RAID_POOLS[theme][fmt])!=3 for fmt in (3,6)):raise ValueError('Each raid needs three teams per format.')
    for fmt in (3,6):
        for index in range(len(RAID_TEAMS[str(fmt)])):
            specs=raid_specs(fmt,index)
            if len(specs)!=fmt or len({c.name for _,c,_ in specs})!=fmt:raise ValueError('Raid formation has invalid/duplicate units.')
            if any(not 0<=card.merge_level<=10 for card,_,_ in specs):raise ValueError('Raid merge level exceeds +10.')
    print(f'Runtime data valid: {len(CHAR_BY_ID)} characters, 35 raid items, 30 raid formations, existing Prefecture/tournament/Gauntlet data, packs and gallery filters.')


if __name__=='__main__':main()
