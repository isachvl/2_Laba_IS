import json
from pathlib import Path
import httpx
from jsonschema import Draft202012Validator

SCHEMA=json.loads(Path('schema.json').read_text(encoding='utf-8'))

def parse_answer(raw,mode,transmitted):
    value=json.loads(raw)
    Draft202012Validator(SCHEMA['json_schema']['schema']).validate(value)
    if any(type(n) is not int or n<0 for n in value['sentences']): raise ValueError('Invalid sentence number')
    if value['label']=='NOINFO' or mode=='llm_only':
        if value['doc_id'] is not None or value['sentences']: raise ValueError('Expected null citation')
    else:
        if value['doc_id'] not in transmitted or not value['sentences']: raise ValueError('Missing provided citation')
        if not set(value['sentences']).issubset(set(transmitted[value['doc_id']])):
            raise ValueError('Citation refers to a sentence not provided')
    return value

class ContextBuilder:
    def __init__(self,base_url):
        self.http=httpx.Client(base_url=base_url.removesuffix('/v1'),timeout=120)
        self.prompts={m:Path(f'prompts/{m}.txt').read_text(encoding='utf-8') for m in ['rag','llm_only']}

    def post(self,path,data):
        response=self.http.post(path,json=data); response.raise_for_status(); return response.json()

    def messages(self,claim,docs,mode):
        body='Claim: '+claim+'\n'
        if mode!='llm_only':
            body+='\nDocuments:\n'
            for d in docs:
                body+=f"\nDocument {d['doc_id']}\nTitle: {d['title']}\n"
                body+='\n'.join(f'[{i}] {s}' for i,s in d['sentences'])+'\n'
        return [{'role':'system','content':self.prompts['llm_only' if mode=='llm_only' else 'rag']},
                {'role':'user','content':body}]

    def count(self,messages):
        prompt=self.post('/apply-template',{'messages':messages})['prompt']
        n=len(self.post('/tokenize',{'content':prompt,'add_special':False,'parse_special':True})['tokens'])
        return n,prompt

    def build(self,claim,documents,mode,budget):
        selected=[]
        assert self.count(self.messages(claim,[],mode))[0]<=budget
        for document in documents:
            if self.count(self.messages(claim,selected+[document],mode))[0]<=budget:
                selected.append(document); continue
            # The first document that cannot fit is cut only at sentence boundaries.
            lo,hi=0,len(document['sentences'])
            while lo<hi:
                mid=(lo+hi+1)//2
                partial={**document,'sentences':document['sentences'][:mid]}
                if self.count(self.messages(claim,selected+[partial],mode))[0]<=budget: lo=mid
                else: hi=mid-1
            if lo:
                selected.append({**document,'sentences':document['sentences'][:lo]})
            break
        messages=self.messages(claim,selected,mode)
        n,prompt=self.count(messages)
        assert n<=budget
        return {'messages':messages,'prompt':prompt,'prompt_tokens':n,'budget':budget,
                'transmitted':{d['doc_id']:[i for i,s in d['sentences']] for d in selected},
                'documents':selected}
