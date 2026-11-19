"""Read already-published previews without constructing a scientific Controller."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from ..errors import read_json, require, safe_file
from .contracts import LIMITS, identifier, integer
from .direct_contracts import manifest_digest
from .preview_files import verify_preview
from .sources import check_source, file_identity


class PublishedPreviewReader:
    def __init__(self, project: Path, store):
        self.project, self.store = project, store
        self.cache = {}

    def source_current(self, source):
        require(type(source.get('fingerprints')) is dict and source['fingerprints'], 'PREVIEW_SOURCE_CHANGED')
        for name in source['fingerprints']:
            require(Path(name).resolve().is_relative_to(self.project.resolve()), 'PREVIEW_SOURCE_CHANGED')
        check_source(source); ref = source['ref']
        if ref['kind'] == 'accepted':
            value = self.store.get(ref['workflowId'])
            require(value['revision'] == ref['revision'] and any(a['attemptId'] == ref['attemptId'] and a['stageId'] == ref['stageId']
                    and a['state'] == 'COMPLETED' and a.get('acceptance', {}).get('manifestSha256') == ref['sourceSha256']
                    for a in value['attempts']), 'PREVIEW_SOURCE_CHANGED')

    def resolve(self, preview_id):
        require(identifier(preview_id) and preview_id.startswith('preview-'), 'INVALID_PREVIEW_ID')
        destination = safe_file(self.project, 'previews/files/' + preview_id)
        cached = self.cache.get(preview_id)
        if cached:
            root, source, manifest, request, metadata, job_path, job_identity = cached
            require(file_identity(root/'manifest.json') == metadata, 'PREVIEW_FILE_CHANGED')
            require(file_identity(job_path/'status.json') == job_identity, 'PREVIEW_NOT_READY')
            self.source_current(source)
            return root, source, manifest, request
        jobs = safe_file(self.project, 'previews/jobs')
        matches = []
        for path in jobs.glob('job-*'):
            record = read_json(safe_file(path, 'status.json'))
            if record['state'] == 'READY' and record.get('previewId') == preview_id: matches.append(path)
        require(matches, 'PREVIEW_NOT_READY')
        path = matches[0]
        request = read_json(safe_file(path, 'request.json'))['request']
        source = read_json(safe_file(destination, 'source-identity.json'))
        require(source.get('ref') == request['source'], 'PREVIEW_SOURCE_CHANGED')
        self.source_current(source)
        manifest = verify_preview(destination, source)
        if len(self.cache) >= 64: self.cache.pop(next(iter(self.cache)))
        self.cache[preview_id] = (destination, source, manifest, request, file_identity(destination/'manifest.json'), path, file_identity(path/'status.json'))
        return destination, source, manifest, request

    def read_bytes(self, preview_id, file_id, offset, limit):
        require(identifier(file_id) and integer(offset, 0, 67108864) and integer(limit, 1, LIMITS['bridgeReadBytes']), 'INVALID_PREVIEW_READ')
        root, source, manifest, _ = self.resolve(preview_id)
        entries = [f for f in manifest['files'] if f['fileId'] == file_id]
        require(len(entries) == 1, 'PREVIEW_FILE_NOT_FOUND'); entry = entries[0]
        require(offset < entry['byteSize'], 'INVALID_PREVIEW_READ')
        descriptor = os.open(safe_file(root, file_id), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno()); require(before.st_size == entry['byteSize'], 'PREVIEW_FILE_CHANGED')
            stream.seek(offset); data = stream.read(min(limit, entry['byteSize'] - offset)); after = os.fstat(stream.fileno())
            require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'PREVIEW_FILE_CHANGED')
        self.source_current(source)
        return dict(previewId=preview_id, fileId=file_id, sourceSha256=manifest['source']['sourceSha256'],
                    manifestSha256=manifest_digest(manifest), offset=offset, nextOffset=offset + len(data),
                    byteSize=entry['byteSize'], fileSha256=entry['sha256'], sha256=hashlib.sha256(data).hexdigest(), data=data)
