"""Run required measurements, analysis, MLflow logging and report generation."""
import argparse
from pathlib import Path
from experiment import Experiment

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume')
    args=parser.parse_args()
    assert Path('index/train_rankings.jsonl').exists(), 'First run prepare_data.py and build_index.py'
    experiment=Experiment(args.resume)
    experiment.run()
    from analyze import analyze
    analyze(experiment.directory)
    from make_report import main
    main()
