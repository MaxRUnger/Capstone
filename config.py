import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key'
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    # Redirect URLs for OAuth (update these with your actual domain)
    REDIRECT_URL = os.environ.get('REDIRECT_URL') or 'http://localhost:5000/auth/callback'