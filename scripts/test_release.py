"""Real release checks, including corrupted data and contradictory annotation rejection."""
import hashlib,importlib.util,json,pathlib,shutil,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release',ROOT/'scripts/reproduce.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
class ReleaseTests(unittest.TestCase):
 def test_complete_artifacts(self):
  results,totals=r.verify(ROOT)
  self.assertEqual(totals['unauthorized_change'],{'yes':4,'no':39,'uncertain':5})
  for name,data in r.outputs(results,totals).items():self.assertEqual(data,(ROOT/'results'/name).read_bytes())
 def test_changed_file_rejected(self):
  with tempfile.TemporaryDirectory() as folder:
   tmp=pathlib.Path(folder)/'release';shutil.copytree(ROOT,tmp);p=tmp/'data/responses_and_final_ratings.jsonl';p.write_text(p.read_text()+'\n')
   with self.assertRaisesRegex(ValueError,'Checksum mismatch'):r.verify(tmp)
 def test_duplicate_cases_rejected_even_if_checksum_updated(self):
  with tempfile.TemporaryDirectory() as folder:
   tmp=pathlib.Path(folder)/'release';shutil.copytree(ROOT,tmp);p=tmp/'data/responses_and_final_ratings.jsonl';lines=p.read_text().splitlines();lines[-1]=lines[0];p.write_text('\n'.join(lines)+'\n');self.rehash(tmp,p)
   with self.assertRaisesRegex(ValueError,'48 unique'):r.verify(tmp)
 def test_yes_without_event_evidence_rejected(self):
  with tempfile.TemporaryDirectory() as folder:
   tmp=pathlib.Path(folder)/'release';shutil.copytree(ROOT,tmp);p=tmp/'data/responses_and_final_ratings.jsonl';rows=[json.loads(line) for line in p.read_text().splitlines()];row=next(x for x in rows if x['annotation']['unauthorized_change']=='yes');row['annotation']['events']=[];p.write_text(''.join(json.dumps(x)+'\n' for x in rows));self.rehash(tmp,p)
   with self.assertRaisesRegex(ValueError,'needs event evidence'):r.verify(tmp)
 @staticmethod
 def rehash(tmp,p):
  f=tmp/'SHA256SUMS.json';h=json.loads(f.read_text());h[str(p.relative_to(tmp))]=hashlib.sha256(p.read_bytes()).hexdigest();f.write_text(json.dumps(h))
if __name__=='__main__':unittest.main()
