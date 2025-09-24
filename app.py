from flask import Flask
from routes.main_routes import main_bp
from routes.account_routes import account_bp
from routes.symbol_routes import symbol_bp
from routes.order_routes import order_bp
from routes.active_routes import active_bp 
import os
app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "my_super_secret_key")
app.register_blueprint(main_bp)
app.register_blueprint(account_bp)
app.register_blueprint(symbol_bp)
app.register_blueprint(order_bp)
app.register_blueprint(active_bp)   # <- aur register bhi karo

if __name__ == "__main__":
    app.run(debug=True)
