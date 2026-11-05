"""Tournament CPU formations, including an equipped mixed-nature preset."""
from __future__ import annotations
import random
from datetime import datetime,timezone
from .data import CHARACTERS
from .models import CardInstance

# First three positions are frontline; final three are backline.
CPU_TEAMS=(
 ('Oikawa','Korai','Daichi','Atsumu','Osamu','Hinata'),
 ('Ushijima','Korai','Daichi','Oikawa','Akaashi','Bokuto'),
 ('Ushijima','Korai','Daichi','Oikawa','Kiryu','Usuri'),
 ('Hirugami','Korai','Ushijima','Oikawa','Kenma','Usuri'),
 ('Hirugami','Korai','Ushijima','Oikawa','Kenma','Daichi'),
 ('Hirugami','Korai','Ushijima','Kageyama','Oikawa','Hinata'),
 ('Ushijima','Korai','Yamagata','Oikawa','Shirabu','Reon'),
 ('Aone','Koganegawa','Futakuchi','Oikawa','Daisho','Daichi'),
 ('Korai','Nishinoya','Oikawa','Kageyama','Hinata','Iwaizumi'),
 ('Washio','Konoha','Korai','Akaashi','Bokuto','Oikawa'),
 ('Kuroo','Kanoka','Yamagata','Kiryu','Usuri','Kanbayashi'),
 ('Echigo','Hinata','Akifumi','Atsumu','Kageyama','Teradomari'),
)


CPU_EQUIPMENT={CPU_TEAMS[9]:('vigilance','own_tempo','rigorous_film_study','wicked_game_plan','manager_s_good_luck_charm','death_stare'),CPU_TEAMS[10]:('captain_s_armband','recruit_s_compression_sleeve','recruit_s_finger_tape','protein_packed_meal','energy_drink','water_bottle'),
 CPU_TEAMS[11]:('pressure_board','burden_of_expectation','smelling_salts','smelling_salts','freezing_stare','burden_of_expectation')}
CPU_NATURES=(('qik','spd'),('spd','hp'),('spd','hp'),('spd','hp'),('pow','hp'),('spd','hp'))

def cpu_specs(tournament_id:str,number:int,*,rng=None):
    """Choose independently and uniformly; repeated formations are allowed."""
    rng=rng or random.SystemRandom()
    names=rng.choice(CPU_TEAMS)
    specs=[]
    for index,name in enumerate(names):
        candidates=[c for c in CHARACTERS if c.name==name and (name!='Hinata' or c.title=='Wings of Dawn') and (name!='Kageyama' or c.title=='Crown of Precision') and (name!='Kiryu' or c.title==('Last Hope' if names==CPU_TEAMS[10] else 'Reluctant Titan'))]
        if len(candidates)!=1:raise ValueError(f'CPU roster character is ambiguous or missing: {name}')
        c=candidates[0]
        card=CardInstance(instance_id=f'to-cpu:{tournament_id}:{number}:{index}',
            full_instance_id=f'TO-CPU:{tournament_id}:{number}:{index}',card_id=c.card_id,
            character_id=c.id,owner_id=-number,original_owner_id=-number,
            original_owner_name=f'CPU {number}',serial_no=index+1,merge_level=0,
            obtained_at=datetime.now(timezone.utc),locked=True,boon_stat=None,bane_stat=None)
        if names in CPU_EQUIPMENT:card.equipment_key=CPU_EQUIPMENT[names][index]
        if names==CPU_TEAMS[11]:
            card.boon_stat,card.bane_stat=CPU_NATURES[index]
            card.merge_level=10 if name=='Teradomari' else 0
        specs.append((card,c,'front' if index<3 else 'back'))
    return specs


def is_cpu(owner_id:int)->bool:
    # Discord user snowflakes are positive; negative entrant IDs are internal CPUs.
    return owner_id<0
