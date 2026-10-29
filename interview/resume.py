"""Extract a single resume in memory; never write uploaded files to disk."""
import base64
import io
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from pypdf import PdfReader


def extract_resume(filename, encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError('附件编码无效。') from None
    if len(data) > 5 * 1024 * 1024:
        raise ValueError('附件不能超过 5 MB。')
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in ('.txt', '.md'):
            text = data.decode('utf-8-sig')
        elif suffix == '.pdf':
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 30:
                raise ValueError('PDF 请使用未加密、最多 30 页的文件。')
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        elif suffix == '.docx':
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo('word/document.xml')
                if info.file_size > 10 * 1024 * 1024:
                    raise ValueError('Word 文档内容过大。')
                root = ET.fromstring(archive.read(info))
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                text = '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))
        else:
            raise ValueError('支持 PDF、DOCX、UTF-8 TXT 或 Markdown 文件。')
    except ValueError:
        raise
    except Exception:
        raise ValueError('附件无法读取，请换成可复制文字的 PDF、DOCX 或 UTF-8 文本。') from None
    if not text.strip():
        raise ValueError('未提取到文字；扫描版 PDF 请先转成可复制文字的文件。')
    if len(text) > 60000:
        raise ValueError('附件文字超过 60000 字符，请精简材料。')
    return text.strip()
