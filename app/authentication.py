import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_KEY"]
service_key = os.environ["SUPABASE_SERVICE_KEY"]

# Anon client for auth (login/signup)
supabase: Client = create_client(url, key)

# Service role client for database operations (bypasses RLS)
supabase_admin: Client = create_client(url, service_key)
