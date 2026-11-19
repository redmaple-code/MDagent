import test from 'node:test';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';
import {verifyRelayToken,decodeRead} from '../visualization/preview-relay.mjs';
test('private relay rejects wrong, absent or rotated world token',()=>{
  assert.throws(()=>verifyRelayToken('wrong','mdw_secret'),/BIO_WORLD_TOKEN_REQUIRED/);
  assert.throws(()=>verifyRelayToken('',undefined),/BIO_WORLD_TOKEN_REQUIRED/);
  verifyRelayToken('mdw_secret','mdw_secret');
});
test('only bounded authenticated bytes with matching digest reach HTTP',()=>{
  const bytes=Buffer.from('xtc');const value={data:bytes.toString('base64'),offset:0,nextOffset:3,sha256:createHash('sha256').update(bytes).digest('hex')};
  assert.deepEqual(decodeRead(value),bytes);assert.throws(()=>decodeRead({...value,sha256:'a'.repeat(64)}),/PREVIEW_CHUNK_CHANGED/);
  assert.throws(()=>decodeRead({...value,data:Buffer.alloc(65537).toString('base64')}),/PREVIEW_CHUNK_CHANGED/);
});
