"""
app/models.py

Aggregator — imports every model so Alembic autogenerate sees them all
and so other modules can `from app.models import User, Project, ...`.

LEGACY MODULES REMOVED:
  - app.ai          (replaced by app/ai/embeddings + lead_agent for the new pipeline)
  - app.agents      (not used in MVP)
  - app.data        (replaced by ingestion + matching + posts)
  - app.missions    (replaced by projects)

If you want to keep those folders around for reference, fine — but
do NOT import their models here. Alembic will only manage what's
imported in this file.
"""
from app.database import Base

from app.auth.models import User
from app.projects.models import Project
from app.keywords.models import Keyword
from app.sources.models import MonitoredSource, ProjectSource
from app.scraper.models import ScraperRun
from app.ingestion.models import RawPost
from app.matching.models import KeywordMatch, PostEmbedding
from app.posts.models import MatchedPost
from app.leads.models import Lead, LeadStatus
from app.feedback.models import Feedback, FeedbackVerification
from app.integrations.models import Integration

__all__ = [
    "Base",
    # Identity
    "User",
    # Project
    "Project",
    # Targets
    "Keyword",
    "MonitoredSource",
    "ProjectSource",
    # Ingestion
    "ScraperRun",
    "RawPost",
    # Matching
    "KeywordMatch",
    "PostEmbedding",
    # Delivery
    "MatchedPost",
    "Lead",
    "LeadStatus",
    # Feedback
    "Feedback",
    "FeedbackVerification",
    # Integrations
    "Integration",
]
