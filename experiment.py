"""LR0-style Experiment/Results: config, metadata, immutable records, resumable calls."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
import httpx
import yaml
from openai import OpenAI
from common import read_jsonl, save_json, sha
from evaluation import gold_dev, oracle_documents
from rag import ContextBuilder, SCHEMA, parse_answer

class Results:
    def __init__(self,directory):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
    def append(self,filename,row):
        with (self.directory/filename).open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False)+'\n'); f.flush()

class Experiment:
    def __init__(self,resume=None):
        self.config=yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8'))
        self.directory=Path(resume) if resume else Path('runs')/datetime.now().strftime('%Y%m%d_%H%M%S')
        self.directory.mkdir(parents=True,exist_ok=True)
        self.server=None; self.log=None; self.builder=None
        self.client=OpenAI(base_url=self.config['base_url'],api_key='none',timeout=600,max_retries=0)
        self.corpus={str(d['doc_id']):d for d in read_jsonl('data/corpus.jsonl')}
        self.results={name:Results(self.directory/name) for name in ['experiment1','experiment2']}
        self.meta={'started':datetime.now().astimezone().isoformat(),'status':'running',
                   'platform':platform.platform(),'processor':platform.processor(),'profile':'LR1 Qwen GGUF; llama.cpp b12110 CPU, 8 threads; port 8082',
                   'generation':{'temperature':0,'max_tokens':128,'seed':42,'cache_prompt':False},
                   'source_hashes':{p.name:sha(p) for p in Path('.').glob('*.py')}}
        if (self.directory/'metadata.json').exists():
            source_hashes=self.meta['source_hashes']
            self.meta=json.loads((self.directory/'metadata.json').read_text(encoding='utf-8'))
            self.meta['status']='running'
            self.meta.setdefault('resumes',[]).append({'time':datetime.now().astimezone().isoformat(),'source_hashes':source_hashes})

    def start_server(self):
        exe=Path(self.config['server_exe']).resolve()
        model=Path(self.config['model_path']).resolve()
        # Reuse LR1 on the original PC; a laptop can download standalone copies.
        if not exe.exists(): exe=(Path('../lab1')/self.config['server_exe']).resolve()
        if not model.exists(): model=(Path('../lab1')/self.config['model_path']).resolve()
        assert exe.exists() and model.exists(), 'Run python download_runtime.py before new measurements'
        try:
            httpx.get('http://127.0.0.1:8082/health',timeout=2)
        except httpx.TransportError: pass
        else: raise RuntimeError('Port 8082 occupied; stop its server before running')
        cmd=[str(exe),'-m',str(model),'--alias',self.config['model'],'-c','16384','-np','1',
             '-ngl',str(self.config['gpu_layers']),'-t','8','-tb','8','-ctk','f16','-ctv','f16','--host','127.0.0.1',
             '--port','8082','--jinja','--cache-ram','0','-lv','4']
        save_json(self.directory/'server_command.json',cmd)
        self.meta['model_sha256']=sha(model)
        self.log=(self.directory/'server.log').open('a',encoding='utf-8')
        env=os.environ.copy()
        self.server=subprocess.Popen(cmd,stdout=self.log,stderr=subprocess.STDOUT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(180):
            if self.server.poll() is not None: raise RuntimeError('Server exited: see server.log')
            try:
                if httpx.get('http://127.0.0.1:8082/health',timeout=2).status_code==200: break
            except httpx.TransportError: pass
            time.sleep(1)
        else: raise TimeoutError('Server not ready')
        self.builder=ContextBuilder(self.config['base_url'])

    def docs(self,ranked,k):
        return [{'doc_id':r['doc_id'],'title':self.corpus[r['doc_id']]['title'],
                 'sentences':list(enumerate(self.corpus[r['doc_id']]['abstract']))} for r in ranked[:k]]

    def call(self,claim,mode,k,context,found,search_s):
        row={'id':claim['id'],'claim':claim['claim'],'mode':mode,'k':k,
             'found':found,'search_s':search_s,**context,'raw_response':None,'parsed':None,'status':'error'}
        start=time.perf_counter()
        try:
            response=self.client.chat.completions.create(model=self.config['model'],messages=context['messages'],
                temperature=0,top_p=1,seed=42,max_tokens=128,response_format=SCHEMA,
                extra_body={'cache_prompt':False,'top_k':0,'min_p':0,'repeat_penalty':1.0})
            row['generation_s']=time.perf_counter()-start
            row['raw_response']=response.choices[0].message.content
            row['finish_reason']=response.choices[0].finish_reason
            row['usage']=response.usage.model_dump() if response.usage else None
            if not response.usage: raise ValueError('Missing usage')
            if response.usage.prompt_tokens!=context['prompt_tokens']: raise ValueError('Tokenizer/usage mismatch or hidden truncation')
            if response.usage.prompt_tokens>context['budget']: raise ValueError('Prompt budget exceeded')
            if response.choices[0].finish_reason=='length': raise ValueError('Output token limit reached')
            row['parsed']=parse_answer(row['raw_response'],mode,context['transmitted'])
            row['status']='ok'
        except Exception as e:
            row.update(error=repr(e),generation_s=time.perf_counter()-start)
        return row

    def run(self):
        Path('latest_run.txt').write_text(str(self.directory),encoding='utf-8')
        for name in ['config.yaml','hypothesis.md','schema.json']:
            if not (self.directory/name).exists(): shutil.copy2(name,self.directory/name)
        shutil.copytree('prompts',self.directory/'prompts',dirs_exist_ok=True)
        for name in ['manifest.json','split.json']: shutil.copy2(Path('data')/name,self.directory/name)
        shutil.copy2('index/passport.json',self.directory/'index_passport.json')
        save_json(self.directory/'metadata.json',self.meta)
        try:
            self.start_server()
            rankings={r['id']:r for r in read_jsonl('index/dev_rankings.jsonl')}
            inputs=read_jsonl('data/dev_inputs.jsonl')
            gold=gold_dev()  # Used only to select oracle evidence in this explicit diagnostic branch.
            train_checks=read_jsonl('index/train_rankings.jsonl')
            plans=[('experiment1','llm_only',0),('experiment1','rag',3),('experiment1','oracle',0),
                   ('experiment2','rag',1),('experiment2','rag',5)]
            for series,mode,k in plans:
                result_path=self.directory/series/'results.jsonl'
                completed={(r['id'],r['mode'],r['k']) for r in read_jsonl(result_path)} if result_path.exists() else set()
                selected=[c for c in inputs if mode!='oracle' or gold[c['id']]['evidence']]
                pending=[c for c in selected if (c['id'],mode,k) not in completed]
                if not pending: continue
                # Format check and warmup use train, never holdout or dev.
                warmmode='llm_only' if mode=='llm_only' else 'rag'
                for train in train_checks:
                    warmdocs=[] if warmmode=='llm_only' else self.docs(train['top5'],max(k,1))
                    warmcontext=self.builder.build(train['claim'],warmdocs,warmmode,2048)
                    warm=self.call(train,warmmode,k,warmcontext,train['top5'][:k],train['search_s'])
                    self.results[series].append('warmup.jsonl',warm)
                    if warm['status']=='ok': break
                else: raise RuntimeError(f"All train format checks failed: {warm.get('error')}")
                for i,claim in enumerate(pending):
                    ranked=rankings[claim['id']]
                    if mode=='oracle': documents=oracle_documents(gold[claim['id']],self.corpus)
                    elif mode=='rag': documents=self.docs(ranked['top5'],k)
                    else: documents=[]
                    context=self.builder.build(claim['claim'],documents,mode,2048)
                    if mode=='oracle':
                        assert all(context['transmitted'].get(d['doc_id'])==[n for n,s in d['sentences']] for d in documents)
                    row=self.call(claim,mode,k,context,ranked['top5'][:k] if mode=='rag' else [],ranked['search_s'] if mode=='rag' else 0)
                    self.results[series].append('results.jsonl',row)
                    print(f"{series} {mode} k={k} {i+1}/{len(pending)} id={claim['id']} {row['status']} "
                          f"tokens={row['prompt_tokens']} time={row['generation_s']:.1f}",flush=True)
            # Defense question 7: rebuilding contexts only, zero model generation calls.
            q7=[]
            for claim in inputs:
                ranked=rankings[claim['id']]
                context=self.builder.build(claim['claim'],self.docs(ranked['top5'],3),'rag',1024)
                q7.append({'id':claim['id'],**context})
            save_json(self.directory/'contexts_1024.json',q7)
            self.meta['status']='completed'
            self.meta.pop('error',None)
        except Exception as e:
            self.meta.update(status='failed',error=repr(e)); raise
        finally:
            if self.builder: self.builder.http.close()
            self.client.close()
            if self.server:
                self.server.terminate()
                try: self.server.wait(timeout=20)
                except subprocess.TimeoutExpired: self.server.kill(); self.server.wait()
            if self.log: self.log.close()
            self.meta['finished']=datetime.now().astimezone().isoformat()
            save_json(self.directory/'metadata.json',self.meta)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--resume'); args=parser.parse_args()
    Experiment(args.resume).run()
