export function createRelayProvider({sessionId,viewportId,fetch:request=globalThis.fetch}){
  return {mode:'relay',readChunk({manifest,fileId,offset,limit,signal,priority='foreground'}){
    return request(`/v1/bio/preview/${manifest.previewId}/files/${fileId}?offset=${offset}&limit=${limit}`,{credentials:'same-origin',redirect:'error',headers:{'x-bio-session':sessionId,'x-bio-viewport':viewportId,'x-bio-source-sha256':manifest.source.sourceSha256,'x-bio-priority':priority},signal});},dispose(){}};
}
