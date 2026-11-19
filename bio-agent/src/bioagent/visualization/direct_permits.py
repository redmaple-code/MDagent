"""Short-lived single-use byte permits in an owned, bounded project runtime."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sys
import time
from ..errors import atomic_json, canonical, read_json, require, safe_file
from ..execution_target import target_sha256,verify_target
from .contracts import identifier, integer
from .direct_contracts import LIMITS, READER_VERSION, manifest_digest, validate_channel, validate_permit

ACTOR_FIELDS = ('tenantId', 'nodeId', 'sessionId', 'viewportId', 'agentEpoch', 'clientId', 'pairingId', 'executionTargetSha256')


def actor_for(binding):
    return {**{k: binding[k] for k in ACTOR_FIELDS if k != 'executionTargetSha256'},
            'executionTargetSha256': target_sha256(binding['target'])}


class DirectPermitStore:
    def __init__(self, project: Path, *, reader, clock=time.time, create=True):
        self.project, self.reader, self.clock = project, reader, clock
        self.root = safe_file(project, 'previews/direct')
        if create: self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(self.root.is_dir(), 'DIRECT_ACCESS_DENIED')
        stat = self.root.stat()
        require(stat.st_uid == os.getuid() and stat.st_mode & 0o077 == 0, 'DIRECT_ACCESS_DENIED')
        self.path = safe_file(self.root, 'runtime.json')
        self.create = create

    @contextmanager
    def locked(self):
        path = safe_file(self.root, 'runtime.lock')
        flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if self.create else 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            stat = os.fstat(descriptor)
            require(stat.st_uid == os.getuid() and stat.st_mode & 0o077 == 0, 'DIRECT_ACCESS_DENIED')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if self.path.exists():
                stat = self.path.stat()
                require(stat.st_uid == os.getuid() and stat.st_mode & 0o077 == 0, 'DIRECT_ACCESS_DENIED')
                data = read_json(safe_file(self.root, 'runtime.json'))
                require(set(data) == {'schemaVersion', 'channels'} and data['schemaVersion'] == 'bio.direct-runtime.v1'
                        and type(data['channels']) is dict and len(data['channels']) <= LIMITS['maxChannels'], 'DIRECT_PROTOCOL_INVALID')
            else:
                require(self.create, 'DIRECT_ACCESS_DENIED')
                data = dict(schemaVersion='bio.direct-runtime.v1', channels={})
            yield data
            atomic_json(self.path, data)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)

    def _channel(self, data, channel_id):
        require(identifier(channel_id), 'DIRECT_ACCESS_DENIED')
        value = data['channels'].get(channel_id)
        require(value is not None and not value['closed'] and self.clock() - value['at'] < LIMITS['pairIdleSeconds'], 'DIRECT_ACCESS_DENIED')
        try:
            target=value['binding']['target']
            marker=read_json(safe_file(self.project,'execution-target.json'))
            require(marker==verify_target(target,self.project,Path(sys.executable)),'DIRECT_BINDING_CHANGED')
        except Exception:
            value['closed']=True;atomic_json(self.path,data)
            raise
        self._source(value)
        return value

    def _source(self, channel):
        _, _, manifest, _ = self.reader.resolve(channel['public']['previewId'])
        require(manifest['source'] == channel['public']['source'] and manifest_digest(manifest) == channel['public']['manifestSha256'], 'DIRECT_SOURCE_CHANGED')
        return manifest

    def check_actor(self, channel_id, actor):
        require(type(actor) is dict and set(actor) == set(ACTOR_FIELDS), 'DIRECT_ACCESS_DENIED')
        with self.locked() as data:
            channel = self._channel(data, channel_id)
            require(actor == actor_for(channel['binding']), 'DIRECT_ACCESS_DENIED')

    def open_channel(self, binding):
        fields = {'tenantId','nodeId','sessionId','viewportId','agentEpoch','clientId','pairingId','target','projectKey',
                  'previewId','source','manifestSha256','transportBinding'}
        require(type(binding) is dict and set(binding) == fields and binding.get('projectKey') == self.project.name
                and type(binding['sessionId']) is str and 1 <= len(binding['sessionId']) <= 256
                and not any(ord(c) < 32 or ord(c) == 127 for c in binding['sessionId']), 'DIRECT_BINDING_CHANGED')
        target = binding['target']; digest = target_sha256(target)
        require(binding['tenantId'] == target['tenantId'] and binding['nodeId'] == target['nodeId']
                and binding['agentEpoch'] == binding['transportBinding'].get('sessionEpoch')
                and hashlib.sha256(binding['sessionId'].encode()).hexdigest() == binding['projectKey'], 'DIRECT_BINDING_CHANGED')
        _, _, manifest, _ = self.reader.resolve(binding['previewId'])
        require(manifest['source'] == binding['source'] and manifest_digest(manifest) == binding['manifestSha256'], 'DIRECT_SOURCE_CHANGED')
        public = dict(schemaVersion='bio.direct-channel.v1', channelId='channel-' + secrets.token_hex(16),
                      targetId=target['targetId'], executionTargetSha256=digest, projectKey=binding['projectKey'],
                      clientId=binding['clientId'], pairingId=binding['pairingId'], previewId=binding['previewId'],
                      uid=target['uid'], hostIdentitySha256=target['hostIdentitySha256'],
                      source=binding['source'], manifestSha256=binding['manifestSha256'], readerVersion=READER_VERSION,
                      transportBinding=binding['transportBinding'])
        validate_channel(public)
        with self.locked() as data:
            for value in data['channels'].values():
                if not value['closed'] and value['binding'] == binding and self.clock() - value['at'] < LIMITS['pairIdleSeconds']:
                    value['at'] = self.clock(); return value['public']
            stale = [key for key, v in data['channels'].items() if v['closed'] or self.clock() - v['at'] >= LIMITS['pairIdleSeconds']]
            if len(data['channels']) >= LIMITS['maxChannels'] and stale: data['channels'].pop(stale[0])
            require(len(data['channels']) < LIMITS['maxChannels'], 'DIRECT_SLOT_LIMIT')
            data['channels'][public['channelId']] = dict(public=public, binding=binding, closed=False, at=self.clock(), slots=[])
        return public

    def issue(self, channel_id, file_id, offset, limit):
        require(identifier(file_id) and integer(offset, 0, 67108864) and integer(limit, 1, LIMITS['permitBytes']), 'INVALID_DIRECT_REQUEST')
        with self.locked() as data:
            channel = self._channel(data, channel_id); manifest = self._source(channel)
            files = [f for f in manifest['files'] if f['fileId'] == file_id]
            require(len(files) == 1, 'DIRECT_ACCESS_DENIED'); entry = files[0]
            require(offset + limit <= entry['byteSize'], 'INVALID_DIRECT_REQUEST')
            slots = channel['slots']; reusable = [i for i, v in enumerate(slots) if v['state'] == 'SPENT' or v['permit']['expiresAtMs'] <= self.clock() * 1000]
            require(len(slots) < 2 or reusable, 'DIRECT_SLOT_LIMIT')
            handle = secrets.token_urlsafe(32)
            permit = dict(schemaVersion='bio.direct-permit.v1', channelId=channel_id, handle=handle, previewId=manifest['previewId'],
                          sourceSha256=manifest['source']['sourceSha256'], manifestSha256=channel['public']['manifestSha256'],
                          fileId=file_id, fileSha256=entry['sha256'], byteSize=entry['byteSize'], offset=offset, limit=limit,
                          expiresAtMs=int((self.clock() + LIMITS['permitSeconds']) * 1000))
            validate_permit(permit)
            slot = dict(handleSha256=hashlib.sha256(handle.encode()).hexdigest(), permit={k: v for k, v in permit.items() if k != 'handle'}, state='PENDING', claimId=None, emitted=0)
            if len(slots) < 2: slots.append(slot)
            else: slots[reusable[0]] = slot
            channel['at'] = self.clock()
        return permit

    def claim(self, channel_id, handle):
        require(type(handle) is str and len(handle) == 43, 'DIRECT_ACCESS_DENIED')
        digest = hashlib.sha256(handle.encode()).hexdigest()
        with self.locked() as data:
            channel = self._channel(data, channel_id)
            slots = [s for s in channel['slots'] if hmac.compare_digest(s['handleSha256'], digest)]
            require(len(slots) == 1, 'DIRECT_ACCESS_DENIED'); slot = slots[0]
            require(slot['permit']['expiresAtMs'] > self.clock() * 1000, 'DIRECT_PERMIT_EXPIRED')
            require(slot['state'] == 'PENDING', 'DIRECT_REPLAY')
            slot.update(state='CLAIMED', claimId=secrets.token_hex(16)); channel['at'] = self.clock()
            return dict(channelId=channel_id, handleSha256=digest, claimId=slot['claimId'], permit=slot['permit'])

    def check(self, claim, offset, size):
        with self.locked() as data:
            channel = self._channel(data, claim['channelId'])
            slots = [s for s in channel['slots'] if s['handleSha256'] == claim['handleSha256'] and s['claimId'] == claim['claimId']]
            require(len(slots) == 1, 'DIRECT_ACCESS_DENIED'); slot = slots[0]; permit = slot['permit']
            require(permit['expiresAtMs'] > self.clock() * 1000, 'DIRECT_PERMIT_EXPIRED')
            require(slot['state'] == 'CLAIMED' and integer(size, 1, LIMITS['frameBytes'])
                    and offset == permit['offset'] + slot['emitted'] and slot['emitted'] + size <= permit['limit'], 'DIRECT_ACCESS_DENIED')
            slot['emitted'] += size
            if slot['emitted'] == permit['limit']: slot['state'] = 'SPENT'
            channel['at'] = self.clock()

    def finish(self, claim):
        with self.locked() as data:
            channel = data['channels'].get(claim['channelId'])
            if channel:
                for slot in channel['slots']:
                    if slot['claimId'] == claim['claimId']: slot['state'] = 'SPENT'

    def close(self, channel_id):
        with self.locked() as data:
            channel = data['channels'].get(channel_id)
            if channel: channel['closed'] = True
        return dict(closed=True, channelId=channel_id)
