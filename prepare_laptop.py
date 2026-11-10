"""Restore two MLflow runs from saved results; no model or GPU is needed."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def restore():
    os.chdir(ROOT)
    os.environ.setdefault('MLFLOW_DISABLE_AGENT_HINT', '1')
    import mlflow
    from mlflow.tracking import MlflowClient
    from mlflow.exceptions import MlflowException
    run_dir = ROOT / Path('latest_run.txt').read_text(encoding='utf-8').strip().replace('\\', '/')
    summary = json.loads((run_dir/'analysis/summary.json').read_text(encoding='utf-8'))
    verification = json.loads((run_dir/'analysis/verification.json').read_text(encoding='utf-8'))
    assert verification['passed'] and verification['calls'] == 420
    assert json.loads((run_dir/'metadata.json').read_text(encoding='utf-8'))['status'] == 'completed'
    mlflow.set_tracking_uri('sqlite:///' + (ROOT/'mlflow.db').as_posix())
    client = MlflowClient()
    exp = client.get_experiment_by_name('lr2')
    exp_id = exp.experiment_id if exp else client.create_experiment('lr2', artifact_location=(ROOT/'mlruns/artifacts').as_uri())
    ids_path = run_dir/'mlflow_runs.json'
    ids = json.loads(ids_path.read_text(encoding='utf-8')) if ids_path.exists() else {}
    for series in ['experiment1', 'experiment2']:
        run_id = ids.get(series)
        if run_id:
            try: client.get_run(run_id)
            except MlflowException: run_id = None
        if not run_id:
            matches = client.search_runs([exp_id], filter_string=f"tags.lab_run = '{run_dir.name}' and tags.series = '{series}'")
            if matches: run_id = matches[0].info.run_id
        with mlflow.start_run(run_id=run_id, experiment_id=exp_id, run_name=f'{run_dir.name}_{series}') as run:
            ids[series] = run.info.run_id
            mlflow.set_tags({'lab_run':run_dir.name, 'series':series, 'data_source':'saved measured results'})
            mlflow.log_params({'budget':2048,'model':'Qwen3-4B-Instruct-2507-Q4_K_M','encoder':'bge-small-en-v1.5','temperature':0})
            if series == 'experiment2':
                names = ['accuracy', 'correct_citation', 'evidence_in_prompt']
                existing = {(name,m.step,m.value) for name in names for m in client.get_metric_history(run.info.run_id,name)}
                for k in [1,3,5]:
                    values = summary['by_k'][str(k)]
                    metrics = {'accuracy':values['accuracy'], 'correct_citation':values['citation'], 'evidence_in_prompt':values['evidence_in_prompt']}
                    pending = {name:value for name,value in metrics.items() if (name,k,value) not in existing}
                    if pending: mlflow.log_metrics(pending,step=k)
            mlflow.log_artifacts(str(run_dir/series), artifact_path='raw')
            mlflow.log_artifacts(str(run_dir/'analysis'), artifact_path='analysis')
            for name in ['report.pdf','defense.md','error_cases.md','README.md']:
                if (ROOT/name).exists(): mlflow.log_artifact(str(ROOT/name),artifact_path='report')
    ids_path.write_text(json.dumps(ids,indent=2),encoding='utf-8')
    print('Ready: experiment lr2, 2 runs, 3 metrics. No model calls were made.')
    print('Run: .\\.venv\\Scripts\\mlflow.exe ui --backend-store-uri sqlite:///mlflow.db --host 127.0.0.1 --port 5001')

if __name__ == '__main__': restore()
