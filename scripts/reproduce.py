"""Offline counts from one final human-rating set. No network or model calls."""
import argparse,collections,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agencybench import analysis
from agencybench.core import object_hash,validate_annotation
SET='final_human_ratings'

def verify(root=ROOT):
 hashes=json.loads((root/'SHA256SUMS.json').read_text())
 for name,expected in hashes.items():
  if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise ValueError('Checksum mismatch: '+name)
 manifest=json.loads((root/'data/manifest.json').read_text());rows=[json.loads(x) for x in (root/'data/responses_and_final_ratings.jsonl').read_text().splitlines()]
 if len(rows)!=48 or len({x['slot_id'] for x in rows})!=48:raise ValueError('Exactly 48 unique cases required')
 slots={s['id']:s for s in manifest['schedule']};scenarios={s['id']:s for s in manifest['scenarios']};labels={}
 for r in rows:
  s=slots[r['slot_id']]
  case={'case_id':s['id'],'prompt':r['prompt'],'response_text':r['response_text'],'scenario':{k:scenarios[s['scenario_id']][k] for k in ('focal_requirement','id','progress_checklist','requirements','title')},'technical_status':r['technical_status']}
  if r['case_sha256']!=object_hash(case):raise ValueError('Case linkage changed')
  # This date identifies the release export, not an inferred human review timestamp.
  validate_annotation({**r['annotation'],'case_id':s['id'],'case_hash':r['case_sha256'],'rater_id':SET,'timestamp':manifest['release_export_date']},case)
  labels[s['id']]=r['annotation']
 if set(labels)!=set(slots):raise ValueError('Slot coverage mismatch')
 requests=[json.loads(line) for line in (root/'data/generation_payloads.jsonl').read_text().splitlines()]
 if len(requests)!=48 or {r['slot_id'] for r in requests}!=set(slots):raise ValueError('Request coverage mismatch')
 prompts={r['slot_id']:r['prompt'] for r in rows}
 for request in requests:
  if object_hash(request['payload'])!=request['payload_sha256']:raise ValueError('Scientific payload hash mismatch')
  if request['payload']['messages'][-1]['content']!=prompts[request['slot_id']]:raise ValueError('Request prompt mismatch')
 frozen=[json.loads(line) for line in (root/'frozen/scenarios/main_v0.1.jsonl').read_text().splitlines()]
 if {r['id']:r for r in frozen}!=scenarios:raise ValueError('Frozen scenario mismatch')
 results=analysis.tables(manifest,{SET:labels})
 if results['confusion']:raise ValueError('Independent agreement unavailable')
 for c in results['cells']:
  assert c['scheduled']==c['annotated']==12 and c['missing_annotations']==0
  assert sum(c['figure_'+v] for v in analysis.CATEGORIES)==12
  for f,values in [('operative_change',('yes','no','uncertain','not_evaluable')),('unauthorized_change',('yes','no','uncertain','not_evaluable')),('progress',('complete','partial','none','uncertain')),('factual_error',('yes','no','uncertain','not_assessed'))]:assert sum(c[f+'_'+v] for v in values)==12
 totals={f:dict(collections.Counter(r['annotation'][f] for r in rows)) for f in ['operative_change','unauthorized_change','progress','factual_error','response_status']}
 if totals!=manifest['expected_overall_counts']:raise ValueError('Published totals differ')
 return results,totals

def outputs(results,totals):
 cells=[{('label_set' if k=='rater' else k):v for k,v in c.items()} for c in results['cells']]
 svg=analysis.svg(results['cells'],SET).decode().replace('Scheduled response counts — rater '+SET,'Final human ratings — 48 scheduled responses')
 return {'cells.csv':analysis.csv_bytes(cells),'counts.svg':svg.encode(),'overall_counts.json':(json.dumps(totals,sort_keys=True,indent=2)+'\n').encode()}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out');p.add_argument('--check',action='store_true');a=p.parse_args();results,totals=verify();generated=outputs(results,totals)
 if a.check:
  for name,data in generated.items():
   if (ROOT/'results'/name).read_bytes()!=data:raise ValueError('Derived result mismatch: '+name)
  print('PASS: 48 linked final human ratings; four cells of 12; frozen hashes, evidence, all denominators and derived outputs agree. No independent-agreement metric.')
 if a.out:
  out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
  for name,data in generated.items():(out/name).write_bytes(data)
  print('Saved deterministic outputs to '+str(out))
 if not a.out and not a.check:print(json.dumps(totals,indent=2))
