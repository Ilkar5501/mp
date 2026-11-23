"""Source-aware protection from effects outside a character's own combat."""
from functools import wraps

def effect_owner(fn):
    @wraps(fn)
    def wrapped(self,unit,*args,**kwargs):
        previous=getattr(self,'effect_source',None);self.effect_source=unit
        try:return fn(self,unit,*args,**kwargs)
        finally:self.effect_source=previous
    return wrapped

class ShieldEffects:
    def foe_effect_allowed(self,target,source=None):
        source=source or getattr(self,'effect_source',None)
        if isinstance(source,tuple):source=next((u for u in self.units if u.uid==source[0]),None)
        if source is None or source.side==target.side:return True
        if not self.gear(target,'fish_cakes'):return True
        ctx=getattr(self,'effect_context',None)
        return ctx is not None and not getattr(self,'in_post_combat',False) and target in (ctx.attacker,ctx.defender)

    def inflict_cooldown(self,source,target,amount=1):
        if source.side!=target.side:
            if not self.foe_effect_allowed(target,source):return False
            if any(self.gear(ally,'chocolate_milk') and ally.lane==target.lane for ally in self.team(target).units):
                return False
        target.cd=min(target.max_cd,target.cd+amount)
        return True
