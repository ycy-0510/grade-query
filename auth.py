from authlib.integrations.starlette_client import OAuth
from starlette.config import Config
from starlette.requests import Request
from starlette.responses import RedirectResponse
import os

# Config reading from env vars
config_data = {
    'GOOGLE_CLIENT_ID': os.environ.get('GOOGLE_CLIENT_ID'),
    'GOOGLE_CLIENT_SECRET': os.environ.get('GOOGLE_CLIENT_SECRET'),
}
starlette_config = Config(environ=config_data)

oauth = OAuth(starlette_config)
oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

def get_current_user(request: Request):
    return request.session.get('user')
