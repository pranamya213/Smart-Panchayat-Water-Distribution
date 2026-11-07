from flask import Flask

def create_app():
    app = Flask(__name__)
    
    from app import db
    db.init_app(app)
    
    from app.routes import main, start_background_monitor
    app.register_blueprint(main)
    start_background_monitor(app)
    
    return app
