from flask import Flask, session
from flask_cors import CORS
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    app.secret_key = config_class.SECRET_KEY

    CORS(app, supports_credentials=True)

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_instructor_mode():
        return {'instructor_mode': session.get('instructor_mode', 'mark')}

    return app