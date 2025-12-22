"""
Routes module for the Finance App.

Contains Flask Blueprints for all API routes.
"""

from .holdings import holdings_bp
from .transactions import transactions_bp
from .screener import screener_bp
from .valuation import valuation_bp
from .summary import summary_bp
from .data import data_bp
from .sec import sec_bp
from .admin import admin_bp


def register_blueprints(app):
    """Register all API blueprints with the Flask app."""
    app.register_blueprint(holdings_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(screener_bp)
    app.register_blueprint(valuation_bp)
    app.register_blueprint(summary_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(sec_bp)
    app.register_blueprint(admin_bp)
