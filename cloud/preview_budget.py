"""One egress clock, round robin within priority, paced before every slice."""
from __future__ import annotations
import asyncio
from collections import OrderedDict, deque
import fcntl
import os
import time


class GlobalPreviewBudget:
    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep, bits_per_second=4000000, lock_path=None):
        self.clock=clock;self.sleep=sleep;self.rate=bits_per_second/8;self.queues={p:OrderedDict() for p in ('foreground','prefetch')}
        self.task=None;self.pid=os.getpid();self.descriptor=None
        self.enabled=all(os.environ.get(k,'1')=='1' for k in ('WEB_CONCURRENCY','UVICORN_WORKERS','MDAGENT_API_WORKERS'))
        self.bytes_sent=0
        if lock_path and self.enabled:
            try:
                self.descriptor=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
                fcntl.flock(self.descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:self.enabled=False

    async def wait(self,owner,byte_count,priority='foreground',*,hold=False):
        if not self.enabled or self.pid!=os.getpid():raise RuntimeError('PREVIEW_SHARED_BUDGET_UNAVAILABLE')
        if type(byte_count) is not int or not 1<=byte_count<=16384 or priority not in self.queues:raise ValueError('INVALID_PREVIEW_BUDGET')
        loop=asyncio.get_running_loop();future=loop.create_future()
        committed=asyncio.Event() if hold else None
        self.queues[priority].setdefault(owner,deque()).append((future,byte_count,committed))
        if self.task is None or self.task.done():self.task=asyncio.create_task(self._pump())
        try:return await future
        except asyncio.CancelledError:
            if committed:committed.set()
            raise

    async def _pump(self):
        while any(self.queues.values()):
            table=next(q for q in self.queues.values() if q)
            owner,queue=table.popitem(last=False);future,count,committed=queue.popleft()
            if queue:table[owner]=queue
            if future.cancelled():continue
            try:
                await self.sleep(count/self.rate)
                if not future.cancelled():
                    self.bytes_sent+=count;future.set_result(committed.set if committed else None)
                    if committed:await committed.wait()
            except BaseException as error:
                if not future.done():future.set_exception(error)
                raise
