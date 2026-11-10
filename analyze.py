import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mlflow
from common import LABELS, read_jsonl, save_json, write_jsonl
from evaluation import gold_dev, claim_label, retrieval_metrics, rationale_present, citation_correct

def correct(row,gold):
    return row['status']=='ok' and row['parsed']['label']==claim_label(gold[row['id']])

def summarize(rows,gold):
    positive=[r for r in rows if gold[r['id']]['evidence']]
    return {'n':len(rows),'accuracy':sum(correct(r,gold) for r in rows)/len(rows),
            'recall':{label:sum(correct(r,gold) for r in rows if claim_label(gold[r['id']])==label)/
                      sum(claim_label(gold[r['id']])==label for r in rows)
                      if any(claim_label(gold[r['id']])==label for r in rows) else None for label in LABELS},
            'citation':sum(r['status']=='ok' and citation_correct(gold[r['id']],r['parsed']) for r in positive)/len(positive),
            'errors':sum(r['status']!='ok' for r in rows)}

def paired(before,after,gold):
    a={r['id']:r for r in before}; b={r['id']:r for r in after}; assert a.keys()==b.keys()
    fixed=[i for i in a if not correct(a[i],gold) and correct(b[i],gold)]
    broken=[i for i in a if correct(a[i],gold) and not correct(b[i],gold)]
    return {'n':len(a),'fixed':len(fixed),'broken':len(broken),'fixed_ids':fixed,'broken_ids':broken,
            'delta_pp':100*(len(fixed)-len(broken))/len(a)}

def analyze(directory):
    directory=Path(directory); out=directory/'analysis'; out.mkdir(exist_ok=True)
    gold=gold_dev(); positives={i for i,c in gold.items() if c['evidence']}
    first=read_jsonl(directory/'experiment1/results.jsonl'); second=read_jsonl(directory/'experiment2/results.jsonl')
    assert len(first)==240 and len(second)==180
    assert len({(r['id'],r['mode'],r['k']) for r in first+second})==420
    assert all(r['id'] in gold for r in first+second)
    assert all(r['prompt_tokens']<=2048 for r in first+second)
    assert all(not r.get('usage') or r['usage']['prompt_tokens']==r['prompt_tokens'] for r in first+second), 'Hidden truncation/tokenizer mismatch'
    rankings={r['id']:r['top5'] for r in read_jsonl('index/dev_rankings.jsonl')}
    assert all(r['found']==rankings[r['id']][:r['k']] for r in first+second if r['mode']=='rag')
    groups={'llm_only':[r for r in first if r['mode']=='llm_only'],
            'oracle':[r for r in first if r['mode']=='oracle']}
    for k in [1,3,5]: groups[f'rag{k}']=[r for r in first+second if r['mode']=='rag' and r['k']==k]
    assert len(groups['oracle'])==60 and all(len(groups[n])==90 for n in ['llm_only','rag1','rag3','rag5'])
    assert {r['id'] for r in groups['oracle']}==positives
    summary={'experiment1':{name:summarize(groups[name],gold) for name in ['llm_only','rag3']},
             'matched60':{name:summarize([r for r in groups[name] if r['id'] in positives],gold) for name in ['llm_only','rag3','oracle']},
             'paired':{'llm_to_rag3':paired(groups['llm_only'],groups['rag3'],gold),
                       'rag3_to_rag1':paired(groups['rag3'],groups['rag1'],gold),
                       'rag3_to_rag5':paired(groups['rag3'],groups['rag5'],gold)},'by_k':{}}
    for k in [1,3,5]:
        rows=groups[f'rag{k}']; retrieval=[]; delivered=[]
        for r in rows:
            if r['id'] in positives:
                retrieval.append(retrieval_metrics(gold[r['id']]['evidence'],[d['doc_id'] for d in r['found']],k))
                delivered.append(rationale_present(gold[r['id']]['evidence'],r['transmitted']))
        summary['by_k'][str(k)]={**summarize(rows,gold),
            **{name:float(np.mean([m[name] for m in retrieval])) for name in ['hit','recall','rr']},
            'evidence_in_prompt':float(np.mean(delivered)),
            'mean_documents':float(np.mean([len(r['transmitted']) for r in rows if r['id'] in positives])),
            'mean_tokens':float(np.mean([r['prompt_tokens'] for r in rows if r['id'] in positives]))}
        # Preserve class recall separately from retrieval recall.
        summary['by_k'][str(k)]['class_recall']=summarize(rows,gold)['recall']
    q7=json.loads((directory/'contexts_1024.json').read_text(encoding='utf-8'))
    small=sum(rationale_present(gold[r['id']]['evidence'],r['transmitted']) for r in q7 if r['id'] in positives)
    r3={r['id']:r for r in groups['rag3']}; r5={r['id']:r for r in groups['rag5']}
    unchanged=[i for i in r3 if r3[i]['prompt']==r5[i]['prompt']]
    summary['budget_check']={'denominator':60,'budget2048_count':round(summary['by_k']['3']['evidence_in_prompt']*60),
                              'budget1024_count':small,'budget1024_fraction':small/60,
                              'unchanged_prompt_k3_to_k5_ids':unchanged}
    categories={'not_found':[],'not_fitted':[],'wrong_with_evidence':[],'noinfo_error':[]}
    for r in groups['rag3']:
        c=gold[r['id']]
        if claim_label(c)=='NOINFO':
            if not correct(r,gold): categories['noinfo_error'].append(r)
        elif not correct(r,gold) or not citation_correct(c,r['parsed']):
            found=set(c['evidence']) & {d['doc_id'] for d in r['found']}
            category='not_found' if not found else ('not_fitted' if not rationale_present(c['evidence'],r['transmitted']) else 'wrong_with_evidence')
            categories[category].append(r)
    examples=[]; used=set(); counts={k:len(v) for k,v in categories.items()}
    for category,rows in categories.items():
        if rows:
            r=rows[0]; used.add(r['id']); examples.append({'category':category,'gold':gold[r['id']], 'result':r})
    for category,rows in categories.items():
        for r in rows:
            if len(examples)>=4: break
            if r['id'] not in used:
                used.add(r['id']); examples.append({'category':category,'gold':gold[r['id']],'result':r})
    assert len(examples)==4, 'Need four actual error cases; inspect other k values if fewer'
    save_json(out/'error_cases.json',{'category_counts_rag3':counts,'missing_categories':[k for k,v in counts.items() if not v], 'cases':examples})
    write_jsonl(out/'evaluated.jsonl',[{'id':r['id'],'mode':r['mode'],'k':r['k'],'gold_label':claim_label(gold[r['id']]),
                  'correct':correct(r,gold),'evidence_in_prompt':rationale_present(gold[r['id']]['evidence'],r['transmitted'])
                  if r['id'] in positives else None,
                  'citation_correct':r['status']=='ok' and citation_correct(gold[r['id']],r['parsed'])} for r in first+second])
    save_json(out/'summary.json',summary)
    save_json(out/'verification.json',{'passed':True,'calls':420,'dev':90,'positive_dev':60,'holdout_used':False,
                                     'within_budget':True,'invalid_answers_in_denominator':True,
                                     'invalid_answers':sum(r['status']!='ok' for r in first+second)})
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'figure.dpi':160})
    fig,axes=plt.subplots(1,2,figsize=(10,3.6),layout='constrained')
    for ax,block,title,names in [(axes[0],summary['experiment1'],'Все 90 утверждений',['llm_only','rag3']),
                               (axes[1],summary['matched60'],'Те же 60 SUPPORT/CONTRADICT',['llm_only','rag3','oracle'])]:
        vals=[100*block[n]['accuracy'] for n in names]
        bars=ax.bar([{'llm_only':'Без поиска','rag3':'RAG k=3','oracle':'Oracle'}[n] for n in names],vals,color=['#64748b','#2563eb','#16a34a'][:len(names)])
        ax.bar_label(bars,fmt='%.1f%%',padding=3); ax.set(ylim=(0,110),ylabel='Верные метки, %',title=title)
        ax.grid(axis='y',alpha=.2); ax.set_axisbelow(True)
    fig.savefig(out/'01_modes.png'); plt.close(fig)
    fig,ax=plt.subplots(figsize=(7.2,3.7),layout='constrained')
    for key,label,style in [('hit','Hit@k','o-'),('recall','Recall@k','^-'),('rr','MRR@k','d-'),('evidence_in_prompt','Обоснование в промпте','s--')]:
        ax.plot([1,3,5],[100*summary['by_k'][str(k)][key] for k in [1,3,5]],style,label=label,markerfacecolor='none',markersize=7 if key=='hit' else 5)
    ax.set(xticks=[1,3,5],xlabel='Число найденных документов k',ylabel='Доля, %',ylim=(0,105),title='Поиск и доставка свидетельства: 60 утверждений')
    ax.legend(fontsize=8); ax.grid(alpha=.2); fig.savefig(out/'02_retrieval.png'); plt.close(fig)
    fig,ax=plt.subplots(figsize=(7.2,3.7),layout='constrained')
    for key,label in [('accuracy','Accuracy: все 90'),('citation','Верная ссылка: 60 с обоснованием')]:
        ax.plot([1,3,5],[100*summary['by_k'][str(k)][key] for k in [1,3,5]],'o-',label=label)
    ax.set(xticks=[1,3,5],xlabel='Число найденных документов k',ylabel='Доля, %',ylim=(0,105),title='Качество RAG при бюджете 2048 токенов')
    ax.legend(fontsize=8); ax.grid(alpha=.2); fig.savefig(out/'03_rag_quality.png'); plt.close(fig)
    mlflow.set_tracking_uri('sqlite:///mlflow.db'); mlflow.set_experiment('lr2')
    ids_path=directory/'mlflow_runs.json'
    ids=json.loads(ids_path.read_text()) if ids_path.exists() else {}
    for series in ['experiment1','experiment2']:
        with mlflow.start_run(run_id=ids.get(series),run_name=f'{directory.name}_{series}') as run:
            ids[series]=run.info.run_id
            mlflow.log_params({'budget':2048,'model':'Qwen3-4B-Instruct-2507-Q4_K_M','encoder':'bge-small-en-v1.5','temperature':0})
            if series=='experiment2':
                client=mlflow.tracking.MlflowClient()
                existing={(key,m.step,m.value) for key in ['accuracy','correct_citation','evidence_in_prompt']
                          for m in client.get_metric_history(run.info.run_id,key)}
                for k in [1,3,5]:
                    g=summary['by_k'][str(k)]
                    metrics={'accuracy':g['accuracy'],'correct_citation':g['citation'],'evidence_in_prompt':g['evidence_in_prompt']}
                    pending={key:value for key,value in metrics.items() if (key,k,value) not in existing}
                    if pending: mlflow.log_metrics(pending,step=k)
            mlflow.log_artifacts(str(directory/series),artifact_path='raw')
            mlflow.log_artifacts(str(out),artifact_path='analysis')
    save_json(ids_path,ids)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

if __name__=='__main__': analyze(Path('latest_run.txt').read_text(encoding='utf-8').strip())
