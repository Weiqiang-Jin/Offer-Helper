import base64
import io
import zipfile
import pytest
from interview.resume import extract_resume


def encode(data):return base64.b64encode(data).decode()


def test_text_resume_and_size_limit():
    assert extract_resume('resume.txt',encode('研究方向：自动驾驶'.encode()))=='研究方向：自动驾驶'
    with pytest.raises(ValueError):extract_resume('resume.txt',encode(b'a'*(5*1024*1024+1)))
    with pytest.raises(ValueError):extract_resume('resume.png',encode(b'image'))
    with pytest.raises(ValueError):extract_resume('resume.pdf',encode(b'not a pdf'))


def test_docx_keeps_paragraphs_and_tables():
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>自动驾驶</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>轨迹预测项目</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    assert extract_resume('resume.docx',encode(data.getvalue()))=='自动驾驶\n轨迹预测项目'
