"""Closed direct-read contracts; no credentials, filesystem or network actions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct

from ..errors import canonical, require
from .contracts import HASH, integer, identifier, keys, validate_manifest, validate_source

CONTRACT = json.loads((Path(__file__).resolve().parents[3] / 'visualization/direct-contracts.json').read_text())
LIMITS = CONTRACT['limits']
READER_VERSION = CONTRACT['readerVersion']


def digest(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def validate_transport_binding(value, code='DIRECT_BINDING_CHANGED'):
    require(keys(value, CONTRACT['bindingFields']) and digest(value['ownerBinding'])
            and identifier(value['sessionEpoch']) and digest(value['executionTargetSha256']), code)
    return value


def validate_direct_request(action, value):
    code = 'INVALID_DIRECT_REQUEST'
    fields = CONTRACT['requestFields'].get(action)
    require(fields is not None and keys(value, fields), code)
    require(all(identifier(value[k]) for k in fields.split() if k.endswith('Id')), code)
    if action == 'direct-open':
        validate_source(value['source'], code)
        require(digest(value['manifestSha256']), code)
    elif action == 'direct-permit':
        require(integer(value['offset'], 0, 67108864) and integer(value['limit'], 1, LIMITS['permitBytes'])
                and value['offset'] + value['limit'] <= 67108864 and digest(value['sourceSha256']), code)
    require(len(canonical(value)) <= LIMITS['jsonBytes'], code)
    return value


def validate_channel(value):
    code = 'DIRECT_BINDING_CHANGED'
    require(keys(value, CONTRACT['channelFields']), code)
    require(value['schemaVersion'] == 'bio.direct-channel.v1' and value['readerVersion'] == READER_VERSION, code)
    require(all(identifier(value[k]) for k in ('channelId', 'targetId', 'clientId', 'pairingId', 'previewId')), code)
    require(all(digest(value[k]) for k in ('executionTargetSha256', 'projectKey', 'manifestSha256')), code)
    require(integer(value['uid'], 0, 2**53-1) and digest(value['hostIdentitySha256']), code)
    validate_source(value['source'], code)
    validate_transport_binding(value['transportBinding'], code)
    require(value['executionTargetSha256'] == value['transportBinding']['executionTargetSha256'], code)
    return value


def validate_permit(value):
    code = 'DIRECT_PROTOCOL_INVALID'
    require(keys(value, CONTRACT['permitFields']) and value['schemaVersion'] == 'bio.direct-permit.v1', code)
    require(all(identifier(value[k]) for k in ('channelId', 'previewId', 'fileId')), code)
    require(all(digest(value[k]) for k in ('sourceSha256', 'manifestSha256', 'fileSha256')), code)
    require(type(value['handle']) is str and re.fullmatch(r'[A-Za-z0-9_-]{43}', value['handle']), code)
    require(integer(value['byteSize'], 1, 67108864) and integer(value['offset'], 0, value['byteSize'] - 1)
            and integer(value['limit'], 1, LIMITS['permitBytes']) and value['offset'] + value['limit'] <= value['byteSize']
            and integer(value['expiresAtMs'], 1, 2**53 - 1), code)
    return value


def manifest_digest(manifest):
    validate_manifest(manifest)
    value = {**manifest, 'frames': [{**f, 'timePs': struct.pack('>d', float(f['timePs']) if f['timePs'] else 0.0).hex()}
                                  for f in manifest['frames']]}
    return hashlib.sha256(canonical(['bio.direct-manifest-identity.v1', value])).hexdigest()
