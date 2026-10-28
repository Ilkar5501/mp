"""Track pending Discord views and failed command tasks for ;refresh."""
from __future__ import annotations
import asyncio
import uuid


class ActivityRegistry:
    def __init__(self):self.views={};self.running={};self.failed={}
    def add_view(self,owners,view,*,key=None):
        for old,(_,candidate) in list(self.views.items()):
            if candidate.is_finished():self.views.pop(old,None)
        key=key or uuid.uuid4().hex
        self.views[key]=(set(owners),view);return key
    def remove_view(self,key):self.views.pop(key,None)
    def refresh_views(self,uid):
        count=0
        for key,(owners,view) in list(self.views.items()):
            if uid in owners:
                view.cancelled=True;view.stop()
                for child in view.children:
                    if hasattr(child,'disabled'):child.disabled=True
                self.views.pop(key,None);count+=1
        return count
    def begin(self,uid,kind):self.running[(uid,kind)]=asyncio.current_task()
    def end(self,uid,kind,failed=False):
        self.running.pop((uid,kind),None)
        if failed:self.failed[(uid,kind)]=True
    def healthy(self,uid,kind):
        task=self.running.get((uid,kind));return bool(task and not task.done())
    def clear_failed(self,uid):
        keys=[key for key in self.failed if key[0]==uid]
        for key in keys:self.failed.pop(key,None)
        for key,task in list(self.running.items()):
            if key[0]==uid and task.done():self.running.pop(key,None)
        return [key[1] for key in keys]
