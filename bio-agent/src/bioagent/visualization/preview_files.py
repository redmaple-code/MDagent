"""Immutable independent XTC files under a finite compute-project budget."""
from __future__ import annotations
from pathlib import Path
import fcntl
import os
from ..errors import atomic_json, read_json, require, safe_file, sha256_file
from .contracts import LIMITS, validate_manifest
from .sources import check_source


def disk_usage(root):
    total=0
    if root.exists():
        for path in root.rglob('*'):
            require(not path.is_symlink(),'PREVIEW_STORAGE_INVALID')
            if path.is_file():total+=path.stat().st_size
    return total


def verify_preview(destination, source=None):
    manifest=validate_manifest(read_json(safe_file(destination,'manifest.json')))
    if source:
        check_source(source);require(manifest['source']==source['ref'],'PREVIEW_SOURCE_CHANGED')
    for f in manifest['files']:
        path=safe_file(destination,f['fileId'])
        require(path.is_file() and path.stat().st_size==f['byteSize'] and sha256_file(path)==f['sha256'],'PREVIEW_FILE_CHANGED')
    return manifest


def write_preview(source, plan, destination: Path, *, quota_bytes=LIMITS['projectDiskBytes'], staging_root=None, quota_root=None):
    check_source(source);require(destination.name==plan['previewId'],'PREVIEW_ID_MISMATCH')
    require(not destination.is_symlink(),'PREVIEW_STORAGE_INVALID')
    if destination.exists():
        require((destination/'manifest.json').exists(),'PREVIEW_INCOMPLETE')
        return verify_preview(destination,source)
    published=destination
    if staging_root is not None:
        destination=staging_root/plan['previewId']
        require(not destination.exists() and not destination.is_symlink(),'PREVIEW_INCOMPLETE')
    count=len(plan['atomIndices']);frames=plan['frames'];root=quota_root or published.parent
    # Reserve conservatively for uncompressed coordinates/topology before creating anything.
    estimate=count*(12*len(frames)+100)+16384+len(frames)*256
    require(disk_usage(root)+estimate<=quota_bytes,'PREVIEW_DISK_BUDGET_EXCEEDED')
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    import numpy as np
    import MDAnalysis as mda
    u=mda.Universe(str(source['topology']));reader=None;reader_index=None
    records=[];mapped=[];processor=None;processed=-1
    def load_raw_frame(frame):
        nonlocal reader,reader_index
        if source['trajectories']:
            if reader_index!=frame['sourceSegment']:
                if reader:reader.close()
                reader=mda.coordinates.DCD.DCDReader(str(source['trajectories'][frame['sourceSegment']]));reader_index=frame['sourceSegment']
            ts=reader[frame['sourceLocalIndex']];u.atoms.positions=ts.positions;u.dimensions=ts.dimensions
        u.trajectory.ts.time=frame['timePs']
    def load_frame(frame):
        nonlocal processed
        if processor is None:
            load_raw_frame(frame);return
        while processed<frame['sourceFrameIndex']:
            next_frame=source['frames'][processed+1]
            load_raw_frame(next_frame)
            u.atoms.positions=processor.unwrap(u.atoms.positions,u.dimensions)
            processed+=1
    def record(file_id, kind, start=None, length=None):
        file=destination/file_id;size=file.stat().st_size
        require(size>0 and (kind!='xtc' or size<=LIMITS['httpMaxBytes']),'PREVIEW_SEGMENT_TOO_LARGE')
        require(disk_usage(root)<=quota_bytes,'PREVIEW_DISK_BUDGET_EXCEEDED')
        records.append(dict(fileId=file_id,byteSize=size,sha256=sha256_file(file),kind=kind,previewFrameStart=start,previewFrameCount=length))
    try:
        if plan.get('pbcRuntime'):
            from .pbc import PbcProcessor
            processor=PbcProcessor(source,plan['pbcRuntime'])
        group=u.atoms[plan['atomIndices']];load_frame(frames[0])
        group.write(str(destination/'topology.pdb'));record('topology.pdb','topology')
        with (destination/'atoms.bin').open('xb') as stream:stream.write(np.asarray(plan['atomIndices'],dtype='<u4').tobytes())
        record('atoms.bin','atom-map')
        capacity=min(LIMITS['windowFrames'],LIMITS['windowBytes']//(count*12),max(1,(LIMITS['httpTargetBytes']-4096)//(count*12+128)))
        for start in range(0,len(frames),capacity):
            chunk=frames[start:start+capacity];fid=f'segment-{start:03d}.xtc'
            with mda.Writer(str(destination/fid),n_atoms=count,precision=3) as writer:
                for local,frame in enumerate(chunk):
                    load_frame(frame)
                    require(np.isfinite(group.positions).all(),'PREVIEW_NONFINITE_COORDINATES')
                    writer.write(group)
                    mapped.append(dict(sourceFrameIndex=frame['sourceFrameIndex'],segmentId=fid,segmentFrameIndex=local,timePs=frame['timePs']))
            record(fid,'xtc',start,len(chunk))
        check_source(source)
        metadata={}
        if processor:
            from .pbc import processing_metadata
            metadata['processing']=processing_metadata(plan['pbcRuntime'])
        manifest=validate_manifest(dict(schemaVersion='bio.preview.v1',previewId=plan['previewId'],source=source['ref'],
            atomCount=count,sourceFrameCount=source['sourceFrameCount'],atomMapFile='atoms.bin',topologyFile='topology.pdb',
            frames=mapped,files=records,precision=3,previewOnly=True,**metadata))
        atomic_json(destination/'identity.json',plan['identity'])
        atomic_json(destination/'source-identity.json',{'ref':source['ref'],'fingerprints':source['fingerprints']})
        require(disk_usage(root)+len(str(manifest))*2<=quota_bytes,'PREVIEW_DISK_BUDGET_EXCEEDED')
        atomic_json(destination/'manifest.json',manifest)
        if staging_root is not None:
            # Failed attempt directories remain intact. Publish only complete, verified files.
            published.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            descriptor=os.open(safe_file(published.parent,'publish.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX)
                require(not published.is_symlink(),'PREVIEW_STORAGE_INVALID')
                if published.exists():
                    manifest=verify_preview(published,source)
                    require(manifest['previewId']==plan['previewId'],'PREVIEW_ID_MISMATCH')
                else:destination.rename(published)
            finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)
        return manifest
    finally:
        if processor:processor.close()
        if reader:reader.close()
        u.trajectory.close()
