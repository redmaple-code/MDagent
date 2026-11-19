import {directLimits as L} from '../web/runtime/visualization/direct-contracts.mjs';
const fail=()=>{throw new Error('DIRECT_PROTOCOL_INVALID');};
export function encodeRecord(header,binary=Buffer.alloc(0)){
  const meta=Buffer.from(JSON.stringify(header),'utf8');
  if(meta.length<1||meta.length>L.jsonBytes||binary.length>L.frameBytes)fail();
  const prefix=Buffer.alloc(8);prefix.writeUInt32BE(meta.length,0);prefix.writeUInt32BE(binary.length,4);
  return Buffer.concat([prefix,meta,binary]);
}
export async function* decodeRecords(stream){
  let buffer=Buffer.alloc(0);
  for await(const chunk of stream){
    if(!Buffer.isBuffer(chunk)&&!(chunk instanceof Uint8Array)||buffer.length+chunk.length>L.jsonBytes+L.frameBytes+65544)fail();
    buffer=Buffer.concat([buffer,chunk]);
    while(buffer.length>=8){
      const metaSize=buffer.readUInt32BE(0),size=buffer.readUInt32BE(4);
      if(metaSize<1||metaSize>L.jsonBytes||size>L.frameBytes)fail();
      const total=8+metaSize+size;if(buffer.length<total)break;
      let header;try{header=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer.subarray(8,8+metaSize)));}catch{fail();}
      if(!header||typeof header!=='object'||Array.isArray(header))fail();
      const binary=buffer.subarray(8+metaSize,total);buffer=buffer.subarray(total);
      yield {header,binary};
    }
  }
  if(buffer.length)fail();
}
