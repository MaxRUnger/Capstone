from flask import Flask
from flask_cors import CORS
from config import Config

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    
    app.secret_key = config_class.SECRET_KEY
    
    CORS(app, supports_credentials=True)
    
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    return app