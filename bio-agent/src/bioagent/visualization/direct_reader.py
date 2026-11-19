"""Fixed direct-preview stdio consumer. No Controller, worker, or scientific writes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import sys
from ..errors import BioError, canonical, read_json, require, safe_file
from ..execution_target import verify_target
from ..store import WorkflowStore
from .direct_contracts import LIMITS, READER_VERSION, validate_channel
from .direct_permits import DirectPermitStore
from .published_reader import PublishedPreviewReader


def _exact(stream, size):
    chunks = []; total = 0
    while total < size:
        part = stream.read(size-total)
        require(part, 'DIRECT_PROTOCOL_INVALID'); chunks.append(part); total += len(part)
    return b''.join(chunks)


def decode_record(stream):
    first = stream.read(1)
    if not first: return None
    prefix = first + _exact(stream, 7)
    meta_size, binary_size = struct.unpack('>II', prefix)
    require(1 <= meta_size <= LIMITS['jsonBytes'] and 0 <= binary_size <= LIMITS['frameBytes'], 'DIRECT_PROTOCOL_INVALID')
    try:
        header = json.loads(_exact(stream, meta_size).decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError): raise BioError('DIRECT_PROTOCOL_INVALID') from None
    require(type(header) is dict, 'DIRECT_PROTOCOL_INVALID')
    return header, _exact(stream, binary_size) if binary_size else b''


def encode_record(header, binary=b''):
    meta = canonical(header)
    require(1 <= len(meta) <= LIMITS['jsonBytes'] and len(binary) <= LIMITS['frameBytes'], 'DIRECT_PROTOCOL_INVALID')
    return struct.pack('>II', len(meta), len(binary)) + meta + binary


def run_reader(projects_root: Path, python: Path, input_stream, output_stream):
    channel = None; permits = None; reader = None; claim = None; request_id = 'invalid'
    def send(header, data=b''):
        output_stream.write(encode_record(header, data)); output_stream.flush()
    try:
        require(projects_root.is_absolute() and projects_root.is_dir() and not projects_root.is_symlink(), 'DIRECT_BINDING_CHANGED')
        while (record := decode_record(input_stream)) is not None:
            header, binary = record; candidate_id = header.get('requestId', 'invalid')
            request_id = candidate_id if type(candidate_id) is str and 1 <= len(candidate_id) <= 128 and not any(ord(c) < 32 for c in candidate_id) else 'invalid'
            require(request_id != 'invalid' and not binary, 'DIRECT_PROTOCOL_INVALID')
            kind = header.get('kind')
            if kind == 'hello':
                require(set(header) == {'kind','requestId','channel'}, 'DIRECT_PROTOCOL_INVALID')
                next_channel = validate_channel(header['channel'])
                if channel is not None:
                    identity = lambda c: [c[k] for k in ('targetId','executionTargetSha256','projectKey','uid','hostIdentitySha256')] + [c['transportBinding'][k] for k in ('ownerBinding','sessionEpoch')]
                    require(identity(next_channel) == identity(channel), 'DIRECT_BINDING_CHANGED')
                channel = next_channel
                project = safe_file(projects_root, channel['projectKey'])
                require(project.is_dir(), 'SSH_IDENTITY_MISMATCH')
                marker = read_json(safe_file(project, 'execution-target.json'))
                verified = verify_target(marker['target'], project, python)
                require(verified['executionTargetSha256'] == channel['executionTargetSha256'] == marker['executionTargetSha256']
                        and marker['target']['uid'] == channel['uid'] and marker['target']['hostIdentitySha256'] == channel['hostIdentitySha256'], 'SSH_IDENTITY_MISMATCH')
                if reader is None:
                    reader = PublishedPreviewReader(project, WorkflowStore(project, read_only=True))
                    permits = DirectPermitStore(project, reader=reader, create=False)
                with permits.locked() as data:
                    existing = permits._channel(data, channel['channelId'])
                    require(existing['public'] == channel, 'DIRECT_BINDING_CHANGED')
                send(dict(kind='hello', requestId=request_id, channelId=channel['channelId'], readerVersion=READER_VERSION,
                          executionTargetSha256=verified['executionTargetSha256'], hostIdentitySha256=marker['target']['hostIdentitySha256'],
                          uid=marker['target']['uid'], projectKey=channel['projectKey']))
            elif kind == 'read':
                require(channel is not None and set(header) == {'kind','requestId','channelId','handle'}
                        and header['channelId'] == channel['channelId'], 'DIRECT_PROTOCOL_INVALID')
                claim = permits.claim(header['channelId'], header['handle']); permit = claim['permit']
                offset, end = permit['offset'], permit['offset'] + permit['limit']
                while offset < end:
                    block = reader.read_bytes(permit['previewId'], permit['fileId'], offset, min(end-offset, LIMITS['frameBytes']))
                    require(block['fileSha256'] == permit['fileSha256'] and block['manifestSha256'] == permit['manifestSha256'], 'DIRECT_FILE_CHANGED')
                    permits.check(claim, offset, len(block['data']))
                    send(dict(kind='data', requestId=request_id, **{k:v for k,v in block.items() if k != 'data'}), block['data'])
                    offset = block['nextOffset']
                permits.finish(claim); claim = None
                send(dict(kind='done', requestId=request_id, nextOffset=offset))
            elif kind == 'close':
                require(set(header) == {'kind','requestId'}, 'DIRECT_PROTOCOL_INVALID')
                send(dict(kind='done', requestId=request_id, nextOffset=None)); return 0
            else: raise BioError('DIRECT_PROTOCOL_INVALID')
        return 0
    except (BrokenPipeError, ConnectionError): return 1
    except Exception as error:
        code = error.code if isinstance(error, BioError) else 'DIRECT_PROTOCOL_INVALID'
        try: send(dict(kind='error', requestId=request_id, code=code))
        except (BrokenPipeError, ConnectionError): pass
        return 1
    finally:
        if claim and permits:
            try: permits.finish(claim)
            except Exception: pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects-root', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    args = parser.parse_args()
    return run_reader(args.projects_root, args.python, sys.stdin.buffer, sys.stdout.buffer)
