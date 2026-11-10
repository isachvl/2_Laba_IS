import tarfile
from pathlib import Path
from common import sha, save_json, read_jsonl
from evaluation import split_data

EXPECTED='11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be'
if __name__=='__main__':
    archive=Path('data/scifact.tar.gz')
    assert sha(archive)==EXPECTED, 'SciFact archive SHA-256 differs from assignment'
    names={'corpus.jsonl','claims_train.jsonl','claims_dev.jsonl'}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name=Path(member.name).name
            if member.isfile() and name in names:
                (Path('data')/name).write_bytes(tar.extractfile(member).read())
    assert len(read_jsonl('data/corpus.jsonl'))==5183
    split=split_data()
    save_json('data/manifest.json',{'source':'https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz',
              'archive_sha256':EXPECTED,'files':{name:sha(Path('data')/name) for name in sorted(names)},
              'corpus_size':5183,**split})
    print('Verified SciFact: 5183 documents, dev=90, holdout=210 (unused).')
