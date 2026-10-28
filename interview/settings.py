"""Local model configuration; credentials never appear in GET responses."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator

SETTINGS_PATH = Path(__file__).resolve().parents[1] / '.model-settings.json'


class ModelSettings(BaseModel):
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr('')
    clear_key: bool = False
    remember: bool = False

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        value = value.strip().rstrip('/')
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('请输入不含用户名、查询参数和片段的基础地址。')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
            raise ValueError('远程接口需使用 HTTPS，本机接口可使用 HTTP。')
        if parsed.path.endswith('/chat/completions'):
            raise ValueError('请填写基础地址，去掉末尾 /chat/completions。')
        return value

    @field_validator('model')
    @classmethod
    def validate_model(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('模型名称不能为空。')
        return value


def public_settings(config):
    return {'base_url': config['llm_url'], 'model': config['llm_model'],
            'has_key': bool(config['llm_key'])}


def load_settings(config):
    if not SETTINGS_PATH.exists():
        return
    body = ModelSettings(**json.loads(SETTINGS_PATH.read_text(encoding='utf-8')))
    config.update(llm_url=body.base_url, llm_model=body.model, llm_key=body.api_key.get_secret_value())


def update_settings(config, body):
    key = body.api_key.get_secret_value().strip()
    if body.clear_key:
        key = ''
    elif not key and body.base_url == config['llm_url'].rstrip('/'):
        key = config['llm_key']
    # Changing destination never silently forwards an old provider's credential.
    if body.remember:
        temporary = SETTINGS_PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps({'base_url': body.base_url, 'model': body.model,
                                          'api_key': key}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(SETTINGS_PATH)
    config.update(llm_url=body.base_url, llm_model=body.model, llm_key=key)
    return public_settings(config)
