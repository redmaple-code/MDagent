import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';
import {ViewStateStore} from '../visualization/view-state.mjs';
const state={schemaVersion:'bio.view-state.v1',sessionId:'s',viewportId:'main',sourceSha256:'a'.repeat(64),source:null,previewRequest:null,previewId:null,timePs:5,sourceFrameIndex:4,representation:'cartoon',selection:[],camera:null};
test('current view stores metadata only and rejects stale replacement',()=>{
  const store=new ViewStateStore(mkdtempSync('/private/tmp/bio-view-state-'));assert.equal(store.load('s'),null);
  assert.throws(()=>store.save('s',{...state,coordinates:[1,2,3]},0),/INVALID_VIEW_STATE/);
  store.save('s',state,0);assert.equal(store.load('s').sourceSha256,state.sourceSha256);
  assert.throws(()=>store.save('s',state,0),/VIEW_STATE_CHANGED/);assert.equal(store.load('other'),null);
});
