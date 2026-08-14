"""
app/utils/exceptions.py
"""
class DomainException(Exception):
    """Base exception for all domain-specific errors."""
    def __init__(self, message: str = "Domain error"):
        self.message = message
        super().__init__(self.message)


class NotFoundException(DomainException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message)


class UnauthorizedException(DomainException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message)


class ConflictException(DomainException):
    def __init__(self, message: str = "Conflict"):
        super().__init__(message)

# Specific Entity Definitions
class ProjectNotFound(NotFoundException):
    def __init__(self, project_id: str):
        super().__init__(f"Project with ID '{project_id}' was not found or access is denied.")

class LeadNotFound(NotFoundException):
    def __init__(self, lead_id: str):
        super().__init__(f"Lead with ID '{lead_id}' was not found.")