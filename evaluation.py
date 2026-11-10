"""Gold annotations are read only here: split, oracle diagnostic and evaluation."""
import hashlib
from common import LABELS, read_jsonl, save_json, write_jsonl

def claim_label(claim):
    if not claim['evidence']: return 'NOINFO'
    labels = {r['label'] for rs in claim['evidence'].values() for r in rs}
    return next(iter(labels)) if len(labels)==1 else None

def split_data():
    claims=read_jsonl('data/claims_dev.jsonl')
    dev=[]; holdout=[]; excluded=[]
    for c in claims:
        if claim_label(c) is None: excluded.append({'id':c['id'],'reason':'mixed evidence labels'})
    for label in LABELS:
        ids=sorted([c['id'] for c in claims if claim_label(c)==label],
                   key=lambda i:hashlib.sha256(f'scifact:{i}'.encode()).hexdigest())
        dev+=ids[:30]; holdout+=ids[30:]
    assert len(dev)==90 and len(holdout)==210 and not set(dev)&set(holdout)
    by_id={c['id']:c for c in claims}
    assert {l:sum(claim_label(by_id[i])==l for i in holdout) for l in LABELS} == {'SUPPORT':94,'CONTRADICT':34,'NOINFO':82}
    save_json('data/split.json', {'method':'sha256(scifact:<id>), first 30 per class',
              'dev_ids':dev,'holdout_ids':holdout,'excluded':excluded})
    write_jsonl('data/dev_inputs.jsonl',[{'id':i,'claim':by_id[i]['claim']} for i in dev])
    train=read_jsonl('data/claims_train.jsonl')
    write_jsonl('data/train_inputs.jsonl',[{'id':c['id'],'claim':c['claim']} for c in train[:5]])
    return {'dev_ids':dev,'holdout_ids':holdout,'excluded':excluded}

def gold_dev():
    ids={c['id'] for c in read_jsonl('data/dev_inputs.jsonl')}
    return {c['id']:c for c in read_jsonl('data/claims_dev.jsonl') if c['id'] in ids}

def retrieval_metrics(relevant, ranked, k):
    relevant=set(map(str,relevant)); selected=list(map(str,ranked[:k]))
    hits=relevant & set(selected)
    rank=next((i+1 for i,d in enumerate(selected) if d in relevant),None)
    return {'hit':int(bool(hits)), 'recall':len(hits)/len(relevant), 'rr':1/rank if rank else 0.0}

def rationale_present(evidence, transmitted):
    return any(set(r['sentences']).issubset(set(transmitted.get(str(doc),[])))
               for doc, rs in evidence.items() for r in rs)

def citation_correct(claim, parsed):
    if not parsed or parsed['label']!=claim_label(claim): return False
    rs=claim['evidence'].get(str(parsed['doc_id']),[])
    return any(set(r['sentences']).issubset(set(parsed['sentences'])) for r in rs)

def oracle_documents(claim, corpus):
    if not claim['evidence']: return None
    doc_id=sorted(claim['evidence'])[0]
    rationale=claim['evidence'][doc_id][0]['sentences']
    doc=corpus[str(doc_id)]
    return [{'doc_id':str(doc_id),'title':doc['title'],
             'sentences':[(i,doc['abstract'][i]) for i in rationale]}]
