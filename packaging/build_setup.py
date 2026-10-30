"""Build a per-user installer using the .NET Framework compiler already in Windows."""
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
import importlib.metadata

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / 'dist' / 'OfferHelper'
    if not (folder / 'OfferHelper.exe').is_file():
        raise RuntimeError('Build OfferHelper.spec first.')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    base = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
            '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.IO.Compression.dll',
            '/r:System.IO.Compression.FileSystem.dll', '/r:Microsoft.CSharp.dll',
            '/win32icon:' + str(ROOT / 'web/assets/app.ico')]
    source = str(ROOT / 'packaging/Setup.cs')
    subprocess.run(base + ['/define:UNINSTALL', '/out:' + str(folder / 'Uninstall.exe'), source], check=True)
    shutil.copy2(ROOT / 'packaging/使用说明.txt', folder / '使用说明.txt')
    notices = folder / 'third-party-licenses'
    notices.mkdir(exist_ok=True)
    packages = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        packages.append(name + '==' + distribution.version)
        for entry in distribution.files or []:
            if '.dist-info/licenses/' in str(entry).replace('\\', '/'):
                source_file = Path(distribution.locate_file(entry))
                relative = str(entry).replace('\\', '/').split('.dist-info/licenses/', 1)[1]
                destination = notices / name / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, destination)
    (notices / 'build-environment.txt').write_text('\n'.join(sorted(packages)), encoding='utf-8')
    archive = ROOT / 'dist/OfferHelper-0.3.0-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as package:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                package.write(path, path.relative_to(folder))
    target = ROOT / 'dist/OfferHelper-Setup-0.3.0-x64.exe'
    subprocess.run(base + ['/resource:' + str(archive) + ',payload.zip', '/out:' + str(target), source], check=True)
    print(target)


if __name__ == '__main__':
    main()
