import json
import time
from pathlib import Path
import numpy as np
import torch
from huggingface_hub import model_info
from sentence_transformers import SentenceTransformer
from common import read_jsonl, save_json, sha, write_jsonl

PREFIX='Represent this sentence for searching relevant passages: '

if __name__=='__main__':
    torch.set_num_threads(8)
    name='BAAI/bge-small-en-v1.5'
    passport_path=Path('index/passport.json')
    revision=json.loads(passport_path.read_text(encoding='utf-8'))['revision'] if passport_path.exists() else model_info(name).sha
    model=SentenceTransformer(name,revision=revision,device='cpu',cache_folder='models/encoder')
    docs=read_jsonl('data/corpus.jsonl')
    texts=[d['title']+'\n'+' '.join(d['abstract']) for d in docs]
    lengths=[len(model.tokenizer(t,truncation=False)['input_ids']) for t in texts]
    vectors=model.encode(texts,batch_size=32,normalize_embeddings=True,convert_to_numpy=True,show_progress_bar=True).astype('float32')
    np.save('index/vectors.npy',vectors)
    save_json('index/doc_ids.json',[str(d['doc_id']) for d in docs])
    write_jsonl('index/truncation.jsonl',[{'doc_id':str(d['doc_id']),'encoder_tokens':n,'truncated':n>model.max_seq_length}
                                       for d,n in zip(docs,lengths)])
    test='A scientific study examines the effect of a treatment on inflammation.'
    raw=model.encode([test,test],normalize_embeddings=True)
    pref=model.encode([PREFIX+test,test],normalize_embeddings=True)
    assert float(raw[0]@raw[1])>.999
    passport={'model':name,'revision':revision,'dimension':vectors.shape[1],
              'max_length':model.max_seq_length,'normalized':True,'query_prefix':PREFIX,'document_prefix':'',
              'document_text':'title + newline + abstract sentences joined by spaces',
              'truncated_count':sum(n>model.max_seq_length for n in lengths),
              'truncated_fraction':sum(n>model.max_seq_length for n in lengths)/len(docs),
              'matrix_shape':list(vectors.shape),'matrix_sha256':sha('index/vectors.npy'),
              'same_text_cosine':float(raw[0]@raw[1]),'with_query_instruction_cosine':float(pref[0]@pref[1]),
              'note':'Identical encoder inputs give cosine ~1; the documented BGE query instruction changes the actual query input.'}
    save_json('index/passport.json',passport)
    manifest=json.loads(Path('data/manifest.json').read_text(encoding='utf-8'))
    manifest['index_passport']=passport
    save_json('data/manifest.json',manifest)
    for split in ['dev','train']:
        rankings=[]
        for claim in read_jsonl(f'data/{split}_inputs.jsonl'):
            start=time.perf_counter()
            query=model.encode(PREFIX+claim['claim'],normalize_embeddings=True).astype('float32')
            scores=vectors@query
            order=np.argsort(-scores,kind='stable')[:5]
            rankings.append({'id':claim['id'],'claim':claim['claim'],'search_s':time.perf_counter()-start,
                             'top5':[{'doc_id':str(docs[i]['doc_id']),'score':float(scores[i])} for i in order]})
        write_jsonl(f'index/{split}_rankings.jsonl',rankings)
    print(json.dumps(passport,indent=2))
