"""Download the pinned CPU server and Qwen weights for NEW experiments only."""
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_NAME = 'Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
MODEL_SHA = '2fde00ce69dd4899c70d020845e2638353015bba0fdf161b3eb965f2bca4464e'
MODEL_URL = 'https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/ae44f08e1392f39c0e474af10c3ff8355c8b6688/' + MODEL_NAME
SERVER_URL = 'https://github.com/ggml-org/llama.cpp/releases/download/b12110/llama-b12110-bin-win-cpu-x64.zip'
SERVER_SHA = '98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2'

def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def download(url, path, expected):
    if path.exists():
        if sha(path) != expected: raise ValueError(f'Unexpected SHA-256: {path}; the existing file was not overwritten')
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    partial = path.with_suffix(path.suffix+'.part')
    print('Downloading',path.name,flush=True)
    request = urllib.request.Request(url,headers={'User-Agent':'LR2-reproducibility'})
    with urllib.request.urlopen(request,timeout=180) as response, partial.open('wb') as target:
        shutil.copyfileobj(response,target,length=1024*1024)
    if sha(partial) != expected: raise ValueError(f'Download SHA-256 mismatch: {partial}')
    partial.replace(path)

if __name__ == '__main__':
    archive = ROOT/'downloads/llama-b12110-bin-win-cpu-x64.zip'
    download(SERVER_URL,archive,SERVER_SHA)
    server = (ROOT/'bin/llama').resolve()
    server.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if not (server/member.filename).resolve().is_relative_to(server): raise ValueError('Unsafe ZIP member')
        z.extractall(server)
    download(MODEL_URL,ROOT/'models'/MODEL_NAME,MODEL_SHA)
    assert (server/'llama-server.exe').exists()
    print('CPU server and model ready. SHA-256 verified.')
