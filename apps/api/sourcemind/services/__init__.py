"""
SourceMind service layer — business logic.

Services never import from the api/ layer (dependency direction: api/ → services/).
All database access goes through services, never directly in route handlers.
"""
