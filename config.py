import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key'
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    # Public base URL for QR / phone upload links. Set on production, e.g. https://claritygrader.net
    # (no trailing slash). If unset and you open the app via localhost, the server tries your LAN IP.
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL')
    # Redirect URLs for OAuth (update these with your actual domain)
    REDIRECT_URL = os.environ.get('REDIRECT_URL') or 'http://localhost:5000/auth/callback'