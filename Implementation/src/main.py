"""
Intelligent Personal Finance Management System
Main entry point

CSCI 6234 - Object-Oriented Design
George Washington University
"""

from flask import Flask
from src.presentation.api import register_routes
from src.infrastructure.database import init_db

def create_app():
    """Application factory pattern."""
    app = Flask(__name__)
    
    # Configuration
    app.config.from_object('config.Config')
    
    # Initialize database
    init_db(app)
    
    # Register routes
    register_routes(app)
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, port=5000)
