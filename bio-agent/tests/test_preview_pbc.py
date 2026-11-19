"""Native PyMOL integration; engineered coordinates, not an MD acceptance run."""
from pathlib import Path
import copy
import importlib
import os
import subprocess
import tempfile
import time
import unittest
from uuid import uuid4
from unittest.mock import patch
import numpy as np
import MDAnalysis as mda
from bioagent.errors import BioError, sha256_file, atomic_json, read_json
from bioagent.visualization.sources import resolve_source
from bioagent.visualization.preview_plan import plan_preview
from bioagent.visualization.preview_files import write_preview
from bioagent.controller import Controller
from bioagent.visualization.service import PreviewService
from bioagent.schema import load_runner
from tests.support import PYTHON
from tests.preview_fixture import make_preview_fixture, refresh_fixture

try:
    pbc = importlib.import_module('bioagent.visualization.pbc')
except ModuleNotFoundError as error:
    if error.name != 'bioagent.visualization.pbc': raise
    pbc = None


class PreviewPbcTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pbc, 'native PBC adapter is not implemented')
        if not os.environ.get('BIO_PREVIEW_PYMOL_PYTHON'):
            self.skipTest('set BIO_PREVIEW_PYMOL_PYTHON to an existing native PyMOL Python')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.runtime=pbc.pbc_runtime()

    def source(self,frames=203):
        f=make_preview_fixture(self.root/'source',frames=frames,with_protein=False)
        u=f['universe'];box=np.array([10,10,10,90,90,90],dtype=np.float32)
        def position(index):
            oxygen=9.7 if index==0 else 10.1+3*(index-1)
            return np.array([[oxygen,5,5],[oxygen+.957,5,5],[oxygen-.239,5.927,5]],dtype=np.float32)%10
        u.atoms.positions=position(0);u.dimensions=box;u.atoms.write(str(f['output']/'topology.pdb'))
        with mda.Writer(str(f['output']/'trajectory.dcd'),n_atoms=3) as writer:
            for i in range(frames):
                u.atoms.positions=position(i);u.dimensions=box;writer.write(u.atoms)
        refresh_fixture(f)
        return f,resolve_source(f['controller'],f['ref'])

    def derive(self,source,**changes):
        request=dict(source=source['ref'],profile='overview',atomSet='all',startFrame=0,
                     endFrame=source['sourceFrameCount']-1,stride=1,pbc='unwrap')
        plan=plan_preview(source,{**request,**changes},pbc_runtime=self.runtime)
        destination=self.root/'previews'/plan['previewId']
        return destination,write_preview(source,plan,destination)

    def decoded(self,destination,manifest):
        result={}
        for entry in manifest['files']:
            if entry['kind']!='xtc':continue
            with mda.Universe(str(destination/'topology.pdb'),str(destination/entry['fileId'])).trajectory as reader:
                for i,ts in enumerate(reader):
                    index=manifest['frames'][entry['previewFrameStart']+i]['sourceFrameIndex']
                    result[index]=ts.positions.copy()
        return result

    def test_native_unwrap_repairs_split_water_before_sparse_frame_selection(self):
        f,source=self.source();before={p:sha256_file(p) for p in f['output'].iterdir()}
        destination,manifest=self.derive(source,stride=40)
        coordinates=self.decoded(destination,manifest)
        for index,wanted in ((0,9.7),(40,127.1),(200,607.1),(202,613.1)):
            self.assertAlmostEqual(coordinates[index][0,0],wanted,places=2)
            self.assertAlmostEqual(np.linalg.norm(coordinates[index][1]-coordinates[index][0]),.957,places=2)
            self.assertAlmostEqual(np.linalg.norm(coordinates[index][2]-coordinates[index][0]),.9573,places=2)
        self.assertEqual(manifest['processing']['unwrap'],'pymol.cmd.pbc_unwrap')
        self.assertEqual(manifest['processing']['makeWhole'],'MDAnalysis')
        self.assertEqual(manifest['source'],source['ref'])
        self.assertEqual(before,{p:sha256_file(p) for p in before})

    def test_detail_uses_prefix_and_raw_preview_remains_original(self):
        _,source=self.source(frames=67)
        detail,dm=self.derive(source,profile='detail',startFrame=63,endFrame=66)
        full,fm=self.derive(source)
        d=self.decoded(detail,dm);a=self.decoded(full,fm)
        np.testing.assert_allclose(d[63],a[63],atol=.01)
        self.assertAlmostEqual(d[63][0,0],196.1,places=2)
        raw,rm=self.derive(source,pbc='raw')
        self.assertNotEqual(dm['previewId'],rm['previewId'])
        self.assertNotIn('processing',rm)
        self.assertAlmostEqual(self.decoded(raw,rm)[1][0,0],.1,places=2)

    def test_runtime_and_mode_are_part_of_preview_identity(self):
        _,source=self.source(frames=2)
        request=dict(source=source['ref'],profile='overview',atomSet='all',startFrame=0,endFrame=1,stride=1)
        raw=plan_preview(source,request)
        self.assertEqual(raw['previewId'],plan_preview(source,{**request,'pbc':'raw'})['previewId'])
        unwrapped=plan_preview(source,{**request,'pbc':'unwrap'},pbc_runtime=self.runtime)
        changed=copy.deepcopy(self.runtime);changed['runtimeSha256']='b'*64
        other=plan_preview(source,{**request,'pbc':'unwrap'},pbc_runtime=changed)
        self.assertNotEqual(raw['previewId'],unwrapped['previewId'])
        self.assertNotEqual(unwrapped['previewId'],other['previewId'])

    def test_missing_runtime_fails_without_silently_using_raw(self):
        with patch.dict(os.environ,{'BIO_PREVIEW_PYMOL_PYTHON':'/missing/pymol-python'}):
            with self.assertRaisesRegex(BioError,'PBC_PYMOL_UNAVAILABLE'):pbc.pbc_runtime()

    def test_invalid_cell_rejects_before_publishing_a_manifest(self):
        _,source=self.source(frames=2)
        with pbc.PbcProcessor(source,self.runtime) as processor:
            with self.assertRaisesRegex(BioError,'PBC_CELL_REQUIRED'):
                processor.unwrap(np.zeros((3,3),dtype=np.float32),None)
        self.assertIsNotNone(processor.process.poll())

    def test_unknown_ligand_bonds_are_not_guessed(self):
        f,_=self.source(frames=2)
        path=f['output']/'topology.pdb';path.write_text(path.read_text().replace('HOH','LIG'));refresh_fixture(f)
        source=resolve_source(f['controller'],f['ref'])
        with self.assertRaisesRegex(BioError,'PBC_TOPOLOGY_REQUIRED'):pbc.PbcProcessor(source,self.runtime)

    def test_disconnected_explicit_ligand_bonds_cannot_claim_a_whole_molecule(self):
        f,_=self.source(frames=2);path=f['output']/'topology.pdb'
        # Every atom is bonded, but two disconnected pairs in one LIG are incomplete.
        u=mda.Universe.empty(4,n_residues=1,atom_resindex=[0]*4,trajectory=True)
        for name,value in dict(names=['C1','C2','C3','C4'],elements=['C']*4,resnames=['LIG'],resids=[1],chainIDs=['A']*4).items():u.add_TopologyAttr(name,value)
        u.atoms.positions=[[9.7,5,5],[.9,5,5],[.1,5,5],[1.3,5,5]];u.dimensions=[10,10,10,90,90,90]
        u.add_TopologyAttr('bonds',[(0,1),(2,3)]);u.atoms.write(str(path))
        with mda.Writer(str(f['output']/'trajectory.dcd'),n_atoms=4) as writer:
            for _ in range(2):writer.write(u.atoms)
        model=read_json(f['output']/'model.json');model['particles']=4;atomic_json(f['output']/'model.json',model)
        refresh_fixture(f);source=resolve_source(f['controller'],f['ref'])
        with self.assertRaisesRegex(BioError,'PBC_TOPOLOGY_REQUIRED'):pbc.PbcProcessor(source,self.runtime)

    def test_changed_native_runtime_is_rejected_and_owned_child_stops(self):
        _,source=self.source(frames=2);changed={**self.runtime,'engineSha256':'f'*64};children=[]
        spawn=pbc.subprocess.Popen
        def remember(*args,**kwargs):
            child=spawn(*args,**kwargs);children.append(child);return child
        with patch.object(pbc.subprocess,'Popen',side_effect=remember):
            with self.assertRaisesRegex(BioError,'PBC_RUNTIME_CHANGED'):pbc.PbcProcessor(source,changed)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())

    def test_dead_native_child_preserves_the_pbc_error_during_cleanup(self):
        _,source=self.source(frames=2)
        processor=pbc.PbcProcessor(source,self.runtime)
        processor.process.kill();processor.process.wait(timeout=3)
        with self.assertRaisesRegex(BioError,'PBC_NATIVE_FAILED'):
            with processor:
                processor.unwrap(np.array([[1,5,5],[1.957,5,5],[.761,5.927,5]],dtype=np.float32),[10,10,10,90,90,90])

    def test_canceled_derivation_stops_native_child_without_publishing(self):
        _,source=self.source(frames=4);instances=[];unwrap=pbc.PbcProcessor.unwrap
        def canceled(processor,*args):
            if not instances:instances.append(processor)
            if processor.calls==2:raise BioError('PREVIEW_CANCELED')
            return unwrap(processor,*args)
        with patch.object(pbc.PbcProcessor,'unwrap',canceled):
            with self.assertRaisesRegex(BioError,'PREVIEW_CANCELED'):self.derive(source)
        self.assertIsNotNone(instances[0].process.poll())
        self.assertEqual(list((self.root/'previews').glob('*/manifest.json')),[])

    def test_owned_cancel_stops_a_real_worker_and_its_native_pymol_child(self):
        f,source=self.source(frames=20000);controller=Controller(f['controller'].project,PYTHON)
        with controller.store._connection(write=True) as db:
            db.execute('INSERT INTO workflows VALUES (?,?,?,?,?)',('wf',1,'c'*64,'COMPLETED','{"protocol":{},"bindings":{}}'))
            db.execute('INSERT INTO stages VALUES (?,?,?,?,?,?)',('wf',1,'production',0,'{"id":"production","kind":"production","parameters":{"dynamics":{"steps":200000}}}','COMPLETED'))
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',('attempt','wf',1,'production','COMPLETED','{"acceptance":{"manifestSha256":"'+source['ref']['sourceSha256']+'"}}'))
        service=PreviewService(controller);operation=str(uuid4())
        request=dict(source=source['ref'],profile='overview',atomSet='all',startFrame=0,endFrame=19999,stride=1,pbc='unwrap')
        job=service.prepare(request,operation_id=operation);process=service.processes[job['jobId']]
        try:
            native=None;deadline=time.monotonic()+15
            while time.monotonic()<deadline and process.poll() is None:
                listing=subprocess.run(['ps','-axo','pid=,ppid=,command='],capture_output=True,text=True,check=True).stdout
                for line in listing.splitlines():
                    columns=line.strip().split(None,2)
                    if len(columns)==3 and int(columns[1])==process.pid and 'pymol_pbc_worker.py' in columns[2] and '--probe' not in columns[2]:
                        native=load_runner().process_identity(int(columns[0]));break
                if native:break
                time.sleep(.02)
            self.assertIsNotNone(native,'real native unwrap child was not observed')
            service.cancel(operation_id=operation);process.wait(timeout=8)
            self.assertEqual(service.status(job['jobId'])['state'],'CANCELED')
            self.assertNotEqual(load_runner().process_identity(native['pid']),native)
            self.assertEqual(list((controller.project/'previews/files').glob('*/manifest.json')),[])
        finally:
            if process.poll() is None:service.cancel(operation_id=operation);process.wait(timeout=8)

    def test_explicit_time_gap_cannot_be_silently_bridged(self):
        f,_=self.source(frames=3)
        (f['output']/'trajectory.times.csv').write_text('stageStep,timePs\n10,0\n20,1\n30,10\n');refresh_fixture(f)
        source=resolve_source(f['controller'],f['ref'])
        with self.assertRaisesRegex(BioError,'PBC_TRAJECTORY_GAP'):pbc.PbcProcessor(source,self.runtime)

    def test_triclinic_and_variable_boxes_use_each_frame_cell(self):
        _,source=self.source(frames=2)
        with pbc.PbcProcessor(source,self.runtime) as processor:
            box=np.array([10,10,10,90,90,60],dtype=np.float32)
            first=np.array([[11,7.794229,5],[11.957,7.794229,5],[10.761,8.721229,5]],dtype=np.float32)
            a=processor.unwrap(first,box)
            second=first+np.array([-4,-8.660254,0],dtype=np.float32)
            b=processor.unwrap(second,box)
            np.testing.assert_allclose(b,a+[1,0,0],atol=1e-4)
        with pbc.PbcProcessor(source,self.runtime) as processor:
            a=processor.unwrap(np.array([[9,5,5],[9.957,5,5],[8.761,5.927,5]],dtype=np.float32),[10,10,10,90,90,90])
            b=processor.unwrap(np.array([[0,5.5,5.5],[.957,5.5,5.5],[11.761,6.427,5.5]],dtype=np.float32),[12,11,11,90,90,90])
            self.assertAlmostEqual(b[0,0],12,places=4)
            self.assertAlmostEqual(np.linalg.norm(b[2]-b[0]),.9573,places=3)
