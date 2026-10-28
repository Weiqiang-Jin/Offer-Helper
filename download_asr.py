"""Download the official streaming bilingual model; never overwrite user files."""
import tarfile
import urllib.request
from pathlib import Path
from interview.local_asr import MODEL_NAME, MODEL_DIR, ready


def main():
    if ready({}):
        print('Local ASR model is ready:', MODEL_DIR)
        return
    MODEL_DIR.parent.mkdir(exist_ok=True)
    archive = MODEL_DIR.parent / (MODEL_NAME + '.tar.bz2')
    url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/' + archive.name
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        print('Downloading official model archive (several hundred MB)...', flush=True)
        request = urllib.request.Request(url, headers={'User-Agent': 'offer-helper'})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                total += len(chunk)
                if total % (20 * 1024 * 1024) == 0:
                    print(f'Downloaded {total // (1024 * 1024)} MB', flush=True)
        partial.replace(archive)
    print('Extracting model...', flush=True)
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            target = (MODEL_DIR.parent / member.name).resolve()
            if not target.is_relative_to(MODEL_DIR.resolve()) or member.issym() or member.islnk():
                raise ValueError('Unexpected archive entry')
        package.extractall(MODEL_DIR.parent, filter='data')
    if not ready({}):
        raise RuntimeError('Missing model files after extraction')
    print('Ready. Restart offer helper, then start listening.', flush=True)


if __name__ == '__main__':
    main()
